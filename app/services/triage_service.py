"""
app/services/triage_service.py
────────────────────────────────
LLM triage service — Phase 3 core.

Pipeline position:
  Alert Engine (Phase 2) → [THIS] Triage Service → Investigation (Phase 4)

State transitions:
  PENDING → TRIAGING → INVESTIGATING   (score 30-89)
  PENDING → TRIAGING → AUTO_CLOSED     (score < 30)
  PENDING → TRIAGING → AWAITING_HUMAN  (score ≥ 90)

If LLM fails: alert reverts to PENDING — never lost, always retryable.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.client import get_llm_client
from app.llm.prompts import TRIAGE_SYSTEM_PROMPT, build_triage_prompt
from app.services.alert_store import store
from app.shared.models import (
    Alert, AlertStatus, AuditEvent,
    KYCProfile, RiskBand, Transaction,
)

logger   = get_logger(__name__)
settings = get_settings()
DATA_DIR = Path(__file__).parent.parent / "data"


# ── Validated triage result ───────────────────────────────────────────────────

class TriageResult(BaseModel):
    """
    Pydantic model for LLM triage output.
    Validates every field — malformed LLM responses raise ValidationError
    before anything is written to the store.
    """
    severity_score     : int = Field(ge=0, le=100)
    severity_band      : RiskBand
    initial_suspicion  : str = Field(min_length=10)
    risk_factors       : list[str] = Field(min_length=1)
    recommended_action : str = Field(
        pattern="^(AUTO_CLOSE|INVESTIGATE|ESCALATE_IMMEDIATELY)$"
    )
    regulatory_flags   : list[str] = Field(default_factory=list)
    confidence         : str = Field(pattern="^(LOW|MEDIUM|HIGH)$")
    llm_provider       : str = ""
    latency_ms         : float = 0.0

    @field_validator("severity_band", mode="before")
    @classmethod
    def coerce_band(cls, v: str) -> RiskBand:
        return RiskBand(v.upper()) if isinstance(v, str) else v


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_transactions() -> dict[str, Transaction]:
    path = DATA_DIR / "transactions.json"
    if not path.exists():
        return {}
    with open(path) as f:
        raw = json.load(f)
    result = {}
    for item in raw:
        try:
            item["amount_aed"] = Decimal(str(item["amount_aed"]))
            tx = Transaction(**item)
            result[tx.tx_id] = tx
        except Exception as e:
            logger.warning(f"Skipping transaction: {e}")
    return result


def _load_kyc_profiles() -> dict[str, KYCProfile]:
    path = DATA_DIR / "kyc_profiles.json"
    if not path.exists():
        return {}
    with open(path) as f:
        raw = json.load(f)
    result = {}
    for item in raw:
        try:
            p = KYCProfile(**item)
            result[p.customer_id] = p
        except Exception as e:
            logger.warning(f"Skipping KYC profile: {e}")
    return result


# ── Triage service ────────────────────────────────────────────────────────────

class TriageService:
    """
    Orchestrates LLM triage for a single alert.

    Initialises once at startup — loads data, creates LLM client.
    The triage() method is called per-alert by the API router.
    """

    def __init__(self) -> None:
        self._llm          = get_llm_client()
        self._transactions = _load_transactions()
        self._kyc_profiles = _load_kyc_profiles()
        logger.info(
            f"TriageService ready | "
            f"transactions={len(self._transactions)} | "
            f"kyc_profiles={len(self._kyc_profiles)} | "
            f"providers={self._llm.available_providers}"
        )

    def triage(self, alert: Alert) -> Optional[TriageResult]:
        """
        Run LLM triage on one PENDING alert.

        Returns TriageResult on success, None on failure.
        Alert is never lost — reverts to PENDING on any error.
        """
        alert_id = str(alert.alert_id)

        if alert.status != AlertStatus.PENDING:
            logger.warning(f"Triage skipped | alert={alert_id} | status={alert.status}")
            return None

        # Mark as in-progress
        alert.status = AlertStatus.TRIAGING
        store.save(alert)
        store.log_event(AuditEvent(
            alert_id    = alert_id,
            event_type  = "TRIAGE_STARTED",
            description = "LLM triage agent started",
            actor       = "triage_service",
            metadata    = {"providers": self._llm.available_providers},
        ))

        try:
            # Gather full context
            transaction = self._transactions.get(alert.tx_id)
            kyc_profile = self._kyc_profiles.get(alert.customer_id)

            # Build prompt and call LLM
            prompt       = build_triage_prompt(alert, transaction, kyc_profile)
            llm_response = self._llm.complete(prompt, TRIAGE_SYSTEM_PROMPT)

            # Validate response
            raw_json = llm_response.parse_json()
            result   = TriageResult(
                **raw_json,
                llm_provider = llm_response.provider,
                latency_ms   = llm_response.latency_ms,
            )

            # Update alert with triage results
            alert.risk_score = result.severity_score
            alert.risk_band  = result.severity_band
            alert.triage_narrative = (
                f"{result.initial_suspicion} "
                f"Risk factors: {'; '.join(result.risk_factors)}. "
                f"Regulatory: {'; '.join(result.regulatory_flags) or 'None'}. "
                f"Confidence: {result.confidence}."
            )

            # State transition
            if result.recommended_action == "AUTO_CLOSE":
                alert.status = AlertStatus.AUTO_CLOSED
                next_state   = "AUTO_CLOSED"
            elif result.recommended_action == "ESCALATE_IMMEDIATELY":
                alert.status = AlertStatus.AWAITING_HUMAN
                next_state   = "AWAITING_HUMAN"
            else:
                alert.status = AlertStatus.INVESTIGATING
                next_state   = "INVESTIGATING"

            store.save(alert)
            store.log_event(AuditEvent(
                alert_id    = alert_id,
                event_type  = "TRIAGE_COMPLETE",
                description = (
                    f"score={result.severity_score} "
                    f"band={result.severity_band.value} "
                    f"action={result.recommended_action} "
                    f"provider={llm_response.provider}"
                ),
                actor    = "triage_service",
                metadata = {
                    "severity_score"    : result.severity_score,
                    "severity_band"     : result.severity_band.value,
                    "recommended_action": result.recommended_action,
                    "confidence"        : result.confidence,
                    "llm_provider"      : llm_response.provider,
                    "latency_ms"        : round(llm_response.latency_ms, 1),
                    "risk_factors"      : result.risk_factors,
                    "regulatory_flags"  : result.regulatory_flags,
                },
            ))

            logger.info(
                f"Triage complete | alert={alert_id} | "
                f"score={result.severity_score} | band={result.severity_band.value} | "
                f"next={next_state} | provider={llm_response.provider} | "
                f"latency={llm_response.latency_ms:.0f}ms"
            )
            return result

        except Exception as e:
            # Revert to PENDING — alert is never lost
            alert.status = AlertStatus.PENDING
            store.save(alert)
            store.log_event(AuditEvent(
                alert_id    = alert_id,
                event_type  = "TRIAGE_FAILED",
                description = f"Triage failed — reverted to PENDING: {e}",
                actor       = "triage_service",
                metadata    = {"error": str(e)},
            ))
            logger.error(f"Triage failed | alert={alert_id} | error={e}")
            return None

    def triage_batch(
        self,
        alerts: list[Alert],
        max_alerts: int = 5,
    ) -> dict[str, Optional[TriageResult]]:
        """Triage multiple PENDING alerts. Default limit 5 for free-tier safety."""
        pending = [a for a in alerts if a.status == AlertStatus.PENDING][:max_alerts]
        logger.info(f"Batch triage | pending={len(pending)} | max={max_alerts}")
        results = {}
        for alert in pending:
            results[str(alert.alert_id)] = self.triage(alert)
        succeeded = sum(1 for r in results.values() if r is not None)
        logger.info(f"Batch complete | processed={len(results)} | succeeded={succeeded}")
        return results

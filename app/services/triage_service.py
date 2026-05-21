"""
app/services/triage_service.py
────────────────────────────────
Triage service — updated for IEEE-CIS Priority 2.

Change: uses unified data_loader instead of direct file reads.
Automatically uses IEEE-CIS transactions when available.
Everything else unchanged.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.client import get_llm_client
from app.llm.prompts import TRIAGE_SYSTEM_PROMPT, build_triage_prompt
from app.services.alert_store import store
from app.services.data_loader import load_kyc_profiles, load_transactions
from app.shared.models import (
    Alert, AlertStatus, AuditEvent, RiskBand,
)

logger   = get_logger(__name__)
settings = get_settings()


class TriageResult(BaseModel):
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


class TriageService:
    """
    LLM triage service.
    IEEE-CIS update: uses unified data_loader for transactions.
    """

    def __init__(self) -> None:
        self._llm          = get_llm_client()
        # load_transactions() auto-selects IEEE-CIS if available
        self._transactions = load_transactions()
        self._kyc_profiles = load_kyc_profiles()

        tx_source = "ieee_cis" if any(
            k.startswith("IEEE-") for k in self._transactions
        ) else "synthetic"

        logger.info(
            f"TriageService ready | "
            f"transactions={len(self._transactions)} ({tx_source}) | "
            f"kyc_profiles={len(self._kyc_profiles)} | "
            f"providers={self._llm.available_providers}"
        )

    def triage(self, alert: Alert) -> Optional[TriageResult]:
        """Run LLM triage on one PENDING alert."""
        alert_id = str(alert.alert_id)

        if alert.status != AlertStatus.PENDING:
            return None

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
            transaction = self._transactions.get(alert.tx_id)
            kyc_profile = self._kyc_profiles.get(alert.customer_id)

            prompt       = build_triage_prompt(alert, transaction, kyc_profile)
            llm_response = self._llm.complete(prompt, TRIAGE_SYSTEM_PROMPT)
            raw_json     = llm_response.parse_json()

            result = TriageResult(
                **raw_json,
                llm_provider = llm_response.provider,
                latency_ms   = llm_response.latency_ms,
            )

            alert.risk_score = result.severity_score
            alert.risk_band  = result.severity_band
            alert.triage_narrative = (
                f"{result.initial_suspicion} "
                f"Risk factors: {'; '.join(result.risk_factors)}. "
                f"Regulatory: {'; '.join(result.regulatory_flags) or 'None'}. "
                f"Confidence: {result.confidence}."
            )

            if result.recommended_action == "AUTO_CLOSE":
                alert.status = AlertStatus.AUTO_CLOSED
            elif result.recommended_action == "ESCALATE_IMMEDIATELY":
                alert.status = AlertStatus.AWAITING_HUMAN
            else:
                alert.status = AlertStatus.INVESTIGATING

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
                f"score={result.severity_score} | "
                f"band={result.severity_band.value} | "
                f"provider={llm_response.provider}"
            )
            return result

        except Exception as e:
            alert.status = AlertStatus.PENDING
            store.save(alert)
            store.log_event(AuditEvent(
                alert_id    = alert_id,
                event_type  = "TRIAGE_FAILED",
                description = f"Failed — reverted to PENDING: {e}",
                actor       = "triage_service",
                metadata    = {"error": str(e)},
            ))
            logger.error(f"Triage failed | alert={alert_id} | {e}")
            return None

    def triage_batch(
        self, alerts: list[Alert], max_alerts: int = 5
    ) -> dict[str, Optional[TriageResult]]:
        pending = [a for a in alerts if a.status == AlertStatus.PENDING][:max_alerts]
        results = {}
        for alert in pending:
            results[str(alert.alert_id)] = self.triage(alert)
        return results

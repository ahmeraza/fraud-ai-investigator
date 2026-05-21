"""
app/services/alert_engine.py
──────────────────────────────
Alert engine — updated for OFAC Priority 1.

Phase 2 rules (unchanged):
  1. HIGH_VALUE             — amount > AED 40,000
  2. SANCTIONED_CORRIDOR    — country in FATF list
  3. DEVICE_MISMATCH        — KYC device changed
  4. NEW_ACCOUNT            — account < 30d + amount > AED 5k

Phase 3 / OFAC addition:
  5. OFAC_NAME_MATCH  [NEW] — merchant name fuzzy-matched against SDN list
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.alert_store import store
from app.services.sanctions_screener import SanctionsScreener
from app.shared.models import (
    Alert, AlertStatus, AlertTrigger, AuditEvent,
    KYCProfile, Transaction,
)

logger   = get_logger(__name__)
settings = get_settings()
DATA_DIR = Path(__file__).parent.parent / "data"


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
            logger.warning(f"Skipping KYC: {e}")
    return result


# ── Rules 1–4 (unchanged from Phase 2) ───────────────────────────────────────

def rule_high_value(tx: Transaction) -> Optional[AlertTrigger]:
    if tx.amount_float > settings.high_value_threshold_aed:
        return AlertTrigger.HIGH_VALUE
    return None


def rule_sanctioned_corridor(tx: Transaction) -> Optional[AlertTrigger]:
    if tx.country in settings.high_risk_countries:
        return AlertTrigger.SANCTIONED_CORRIDOR
    return None


def rule_device_mismatch(tx: Transaction, kyc: dict[str, KYCProfile]) -> Optional[AlertTrigger]:
    p = kyc.get(tx.customer_id)
    if p and p.has_device_mismatch:
        return AlertTrigger.DEVICE_MISMATCH
    return None


def rule_new_account(tx: Transaction, kyc: dict[str, KYCProfile], min_amount: float = 5_000.0) -> Optional[AlertTrigger]:
    p = kyc.get(tx.customer_id)
    if p and p.is_new_account and tx.amount_float > min_amount:
        return AlertTrigger.NEW_ACCOUNT
    return None


# ── Rule 5: OFAC name match [NEW] ─────────────────────────────────────────────

def rule_ofac_name_match(
    tx       : Transaction,
    screener : SanctionsScreener,
    threshold: int = 75,
) -> Optional[AlertTrigger]:
    """
    Screen the transaction's merchant name against the OFAC SDN list.
    Fires when fuzzy match score >= threshold (default 75 = STRONG match).

    Why screen the merchant name?
      The sanctioned_corridor rule catches known high-risk countries
      but misses sanctioned entities operating through third countries
      (e.g. Iranian front companies registered in UAE — country=AE,
      not flagged by corridor rule, but merchant name is in SDN list).
    """
    if not tx.merchant:
        return None
    result = screener.screen(tx.merchant, country=tx.country)
    if result.is_hit and result.best_score >= threshold:
        logger.warning(
            f"OFAC name match | tx={tx.tx_id} | merchant={tx.merchant} | "
            f"match={result.top_match.primary_name if result.top_match else 'N/A'} | "
            f"score={result.best_score}"
        )
        return AlertTrigger.SANCTIONED_CORRIDOR
    return None


# ── Alert engine ──────────────────────────────────────────────────────────────

class AlertEngine:
    """
    Evaluates a transaction against all 5 rules and creates alerts.
    One Alert per triggered rule (deduplicated by trigger type).
    """

    def __init__(self) -> None:
        self._kyc_profiles = _load_kyc_profiles()
        self._screener     = SanctionsScreener()
        logger.info(
            f"AlertEngine ready | "
            f"kyc_profiles={len(self._kyc_profiles)} | "
            f"sanctions_entities={self._screener.entity_count:,} | "
            f"name_variants={self._screener.name_variant_count:,}"
        )

    def evaluate(self, tx: Transaction) -> list[Alert]:
        """Run all 5 rules. Return list of alerts created (one per rule that fired)."""
        triggers_seen: set[AlertTrigger] = set()
        alerts: list[Alert] = []

        candidates = [
            rule_high_value(tx),
            rule_sanctioned_corridor(tx),
            rule_device_mismatch(tx, self._kyc_profiles),
            rule_new_account(tx, self._kyc_profiles),
            rule_ofac_name_match(tx, self._screener),
        ]

        for trigger in (t for t in candidates if t is not None):
            if trigger in triggers_seen:
                continue
            triggers_seen.add(trigger)

            alert = Alert(
                tx_id       = tx.tx_id,
                customer_id = tx.customer_id,
                trigger     = trigger,
                status      = AlertStatus.PENDING,
            )
            store.save(alert)
            store.log_event(AuditEvent(
                alert_id    = str(alert.alert_id),
                event_type  = "ALERT_CREATED",
                description = (
                    f"{trigger.value} | "
                    f"AED {tx.amount_float:,.0f} | "
                    f"{tx.country} | {tx.merchant[:40]}"
                ),
                actor    = "alert_engine",
                metadata = {
                    "tx_id"     : tx.tx_id,
                    "trigger"   : trigger.value,
                    "amount_aed": float(tx.amount_aed),
                    "country"   : tx.country,
                    "merchant"  : tx.merchant,
                },
            ))
            alerts.append(alert)
            logger.info(f"Alert created | {alert.alert_id} | {trigger.value} | tx={tx.tx_id}")

        return alerts

    def evaluate_batch(self, transactions: list[Transaction]) -> list[Alert]:
        return [a for tx in transactions for a in self.evaluate(tx)]

    def reload_data(self) -> None:
        self._kyc_profiles = _load_kyc_profiles()
        self._screener     = SanctionsScreener()
        logger.info("AlertEngine reloaded")

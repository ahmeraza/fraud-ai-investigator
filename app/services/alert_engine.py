"""
app/services/alert_engine.py
──────────────────────────────
Alert engine — updated for IEEE-CIS Priority 2.

Change: uses unified DataLoader instead of direct file reads.
This means the engine automatically uses IEEE-CIS real data
when available, falling back to synthetic when not.

All 5 rules unchanged.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.alert_store import store
from app.services.data_loader import load_kyc_profiles
from app.services.sanctions_screener import SanctionsScreener
from app.shared.models import (
    Alert, AlertStatus, AlertTrigger, AuditEvent,
    KYCProfile, Transaction,
)

logger   = get_logger(__name__)
settings = get_settings()


# ── Rules 1–5 (unchanged) ────────────────────────────────────────────────────

def rule_high_value(tx: Transaction) -> Optional[AlertTrigger]:
    """Amount > AED 40,000 CBUAE reporting threshold."""
    if tx.amount_float > settings.high_value_threshold_aed:
        return AlertTrigger.HIGH_VALUE
    return None


def rule_sanctioned_corridor(tx: Transaction) -> Optional[AlertTrigger]:
    """Country in FATF 2024 high-risk jurisdiction list."""
    if tx.country in settings.high_risk_countries:
        return AlertTrigger.SANCTIONED_CORRIDOR
    return None


def rule_device_mismatch(
    tx: Transaction, kyc: dict[str, KYCProfile]
) -> Optional[AlertTrigger]:
    """KYC device fingerprint changed — possible account takeover."""
    p = kyc.get(tx.customer_id)
    if p and p.has_device_mismatch:
        return AlertTrigger.DEVICE_MISMATCH
    return None


def rule_new_account(
    tx: Transaction,
    kyc: dict[str, KYCProfile],
    min_amount: float = 5_000.0,
) -> Optional[AlertTrigger]:
    """Account < 30 days old + transaction > AED 5k."""
    p = kyc.get(tx.customer_id)
    if p and p.is_new_account and tx.amount_float > min_amount:
        return AlertTrigger.NEW_ACCOUNT
    return None


def rule_ofac_name_match(
    tx       : Transaction,
    screener : SanctionsScreener,
    threshold: int = 75,
) -> Optional[AlertTrigger]:
    """Merchant name fuzzy-matched against OFAC SDN list."""
    if not tx.merchant:
        return None
    result = screener.screen(tx.merchant, country=tx.country)
    if result.is_hit and result.best_score >= threshold:
        return AlertTrigger.SANCTIONED_CORRIDOR
    return None


# ── Alert engine ──────────────────────────────────────────────────────────────

class AlertEngine:
    """
    Evaluates transactions against all 5 rules.

    IEEE-CIS update: uses unified data_loader so the engine
    automatically uses real transaction data when available.
    KYC profiles remain synthetic (IEEE-CIS has no KYC equivalent).
    """

    def __init__(self) -> None:
        # KYC always synthetic — IEEE-CIS has no identity data
        self._kyc_profiles = load_kyc_profiles()
        self._screener     = SanctionsScreener()
        logger.info(
            f"AlertEngine ready | "
            f"kyc_profiles={len(self._kyc_profiles)} | "
            f"sanctions_entities={self._screener.entity_count:,} | "
            f"name_variants={self._screener.name_variant_count:,}"
        )

    def evaluate(self, tx: Transaction) -> list[Alert]:
        """Run all 5 rules. Return alerts for each rule that fires."""
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
                    "source"    : "ieee_cis" if tx.tx_id.startswith("IEEE-") else "synthetic",
                },
            ))
            alerts.append(alert)
            logger.info(
                f"Alert | {alert.alert_id} | {trigger.value} | "
                f"tx={tx.tx_id} | AED {tx.amount_float:,.0f}"
            )

        return alerts

    def evaluate_batch(self, transactions: list[Transaction]) -> list[Alert]:
        return [a for tx in transactions for a in self.evaluate(tx)]

    def reload_data(self) -> None:
        self._kyc_profiles = load_kyc_profiles()
        self._screener     = SanctionsScreener()
        logger.info("AlertEngine reloaded")

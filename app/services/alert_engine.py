"""
app/services/alert_engine.py
──────────────────────────────
Deterministic rule-based alert engine.

This runs BEFORE any LLM calls — fast, cheap, and explainable.
Every transaction is evaluated against all rules. If any rule fires,
an Alert is created and saved to the store.

Rules implemented (Week 2):
  1. HIGH_VALUE       — amount exceeds UAE Central Bank reporting threshold (AED 40k)
  2. SANCTIONED_CORRIDOR — transaction involves a FATF/UN high-risk country
  3. DEVICE_MISMATCH  — customer's device fingerprint doesn't match KYC record
  4. NEW_ACCOUNT      — account is less than 30 days old + transaction > AED 5k
  5. VELOCITY         — placeholder for Week 3 (requires transaction history)

Design principle:
  Rules are fast O(1) checks. The LLM (Week 3) adds narrative and nuance.
  Separating them means the API stays responsive even if the LLM is slow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.alert_store import store
from app.shared.models import (
    Alert,
    AlertStatus,
    AlertTrigger,
    AuditEvent,
    KYCProfile,
    SanctionsEntry,
    Transaction,
)

logger = get_logger(__name__)
settings = get_settings()

# ── Data loaders ──────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "data"


def _load_kyc_profiles() -> dict[str, KYCProfile]:
    """Load KYC profiles keyed by customer_id."""
    path = DATA_DIR / "kyc_profiles.json"
    if not path.exists():
        logger.warning("kyc_profiles.json not found — run generate_data.py first")
        return {}
    with open(path) as f:
        raw = json.load(f)
    profiles = {}
    for item in raw:
        try:
            p = KYCProfile(**item)
            profiles[p.customer_id] = p
        except Exception as e:
            logger.warning(f"Skipping invalid KYC profile: {e}")
    return profiles


def _load_sanctions() -> list[SanctionsEntry]:
    """Load the sanctions watchlist."""
    path = DATA_DIR / "sanctions_watchlist.json"
    if not path.exists():
        logger.warning("sanctions_watchlist.json not found")
        return []
    with open(path) as f:
        raw = json.load(f)
    entries = []
    for item in raw:
        try:
            entries.append(SanctionsEntry(**item))
        except Exception as e:
            logger.warning(f"Skipping invalid sanctions entry: {e}")
    return entries


# ── Individual rule functions ─────────────────────────────────────────────────


def rule_high_value(tx: Transaction) -> Optional[AlertTrigger]:
    """
    Rule 1: High-value transaction.
    Fires when amount exceeds the UAE Central Bank reporting threshold (AED 40,000).
    This is the single most common trigger in UAE AML compliance.
    """
    if tx.amount_float > settings.high_value_threshold_aed:
        logger.debug(
            f"HIGH_VALUE rule fired: {tx.tx_id} amount={tx.amount_float} "
            f"threshold={settings.high_value_threshold_aed}"
        )
        return AlertTrigger.HIGH_VALUE
    return None


def rule_sanctioned_corridor(tx: Transaction) -> Optional[AlertTrigger]:
    """
    Rule 2: Sanctioned or high-risk country corridor.
    Fires when the transaction country is on the FATF high-risk list
    or under active UN / OFAC / EU sanctions.
    """
    if tx.country in settings.high_risk_countries:
        logger.debug(
            f"SANCTIONED_CORRIDOR rule fired: {tx.tx_id} country={tx.country}"
        )
        return AlertTrigger.SANCTIONED_CORRIDOR
    return None


def rule_device_mismatch(
    tx: Transaction,
    kyc_profiles: dict[str, KYCProfile],
) -> Optional[AlertTrigger]:
    """
    Rule 3: Device fingerprint mismatch.
    Fires when the customer's current device doesn't match their KYC-verified device.
    Strong signal for account takeover fraud.
    """
    profile = kyc_profiles.get(tx.customer_id)
    if profile and profile.has_device_mismatch:
        logger.debug(
            f"DEVICE_MISMATCH rule fired: {tx.tx_id} customer={tx.customer_id}"
        )
        return AlertTrigger.DEVICE_MISMATCH
    return None


def rule_new_account(
    tx: Transaction,
    kyc_profiles: dict[str, KYCProfile],
    min_amount: float = 5_000.0,
) -> Optional[AlertTrigger]:
    """
    Rule 4: New account with significant transaction.
    Fires when account is < 30 days old AND transaction > AED 5,000.
    New accounts making large transactions are a common fraud pattern.
    """
    profile = kyc_profiles.get(tx.customer_id)
    if profile and profile.is_new_account and tx.amount_float > min_amount:
        logger.debug(
            f"NEW_ACCOUNT rule fired: {tx.tx_id} "
            f"account_age={profile.account_age_days}d amount={tx.amount_float}"
        )
        return AlertTrigger.NEW_ACCOUNT
    return None


# ── Main engine function ──────────────────────────────────────────────────────


class AlertEngine:
    """
    Evaluates a transaction against all rules and creates alerts.

    Usage:
        engine = AlertEngine()
        alerts = engine.evaluate(transaction)
        # Returns list of Alert objects (one per rule that fired)
    """

    def __init__(self) -> None:
        self._kyc_profiles = _load_kyc_profiles()
        self._sanctions = _load_sanctions()
        logger.info(
            f"AlertEngine initialised: "
            f"{len(self._kyc_profiles)} KYC profiles, "
            f"{len(self._sanctions)} sanctions entries"
        )

    def evaluate(self, tx: Transaction) -> list[Alert]:
        """
        Run all rules against a transaction.
        Returns a list of Alert objects for every rule that fired.
        One transaction can trigger multiple alerts.
        """
        alerts_created: list[Alert] = []

        # Run all rules — collect triggers
        triggers: list[AlertTrigger] = []

        if t := rule_high_value(tx):
            triggers.append(t)

        if t := rule_sanctioned_corridor(tx):
            triggers.append(t)

        if t := rule_device_mismatch(tx, self._kyc_profiles):
            triggers.append(t)

        if t := rule_new_account(tx, self._kyc_profiles):
            triggers.append(t)

        # Create one Alert per trigger
        for trigger in triggers:
            alert = Alert(
                tx_id=tx.tx_id,
                customer_id=tx.customer_id,
                trigger=trigger,
                status=AlertStatus.PENDING,
            )
            store.save(alert)
            store.log_event(AuditEvent(
                alert_id=str(alert.alert_id),
                event_type="ALERT_CREATED",
                description=(
                    f"Alert created by rule engine: {trigger.value} | "
                    f"amount=AED {tx.amount_float:,.2f} | country={tx.country}"
                ),
                actor="alert_engine",
                metadata={
                    "tx_id": tx.tx_id,
                    "trigger": trigger.value,
                    "amount_aed": float(tx.amount_aed),
                    "country": tx.country,
                },
            ))
            alerts_created.append(alert)
            logger.info(
                f"Alert created: {alert.alert_id} | "
                f"trigger={trigger.value} | tx={tx.tx_id}"
            )

        if not triggers:
            logger.debug(f"No alerts for tx {tx.tx_id} — all rules passed")

        return alerts_created

    def evaluate_batch(self, transactions: list[Transaction]) -> list[Alert]:
        """Evaluate a list of transactions and return all alerts created."""
        all_alerts = []
        for tx in transactions:
            all_alerts.extend(self.evaluate(tx))
        return all_alerts

    def reload_data(self) -> None:
        """Reload KYC and sanctions data from disk — useful in development."""
        self._kyc_profiles = _load_kyc_profiles()
        self._sanctions = _load_sanctions()
        logger.info("AlertEngine data reloaded from disk")

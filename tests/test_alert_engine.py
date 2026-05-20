"""
tests/test_alert_engine.py
───────────────────────────
Unit tests for all alert engine rules.

Tests are grouped by rule — each class tests one rule in isolation.
Run with:
    uv run pytest tests/test_alert_engine.py -v
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.services.alert_engine import (
    AlertEngine,
    rule_device_mismatch,
    rule_high_value,
    rule_new_account,
    rule_sanctioned_corridor,
)
from app.services.alert_store import store
from app.shared.models import AlertTrigger, KYCProfile, RiskBand, Transaction


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_tx(**kwargs) -> Transaction:
    """Helper to build a Transaction with sensible defaults."""
    defaults = {
        "tx_id": "TX-TEST-001",
        "customer_id": "CUST001",
        "amount_aed": Decimal("5000.00"),
        "currency": "AED",
        "merchant": "Dubai Mall",
        "country": "AE",
        "timestamp": datetime.utcnow(),
        "is_flagged": False,
    }
    defaults.update(kwargs)
    return Transaction(**defaults)


def make_kyc(**kwargs) -> KYCProfile:
    """Helper to build a KYCProfile with sensible defaults."""
    defaults = {
        "customer_id": "CUST001",
        "name": "Ahmed Al Mansoori",
        "nationality": "AE",
        "account_age_days": 365,
        "device_id": "device-abc-123",
        "last_known_device": "device-abc-123",
        "risk_tier": RiskBand.LOW,
    }
    defaults.update(kwargs)
    return KYCProfile(**defaults)


@pytest.fixture(autouse=True)
def clear_store():
    """Clear the alert store before every test for isolation."""
    store.clear()
    yield
    store.clear()


# ── Rule 1: High value ────────────────────────────────────────────────────────


class TestRuleHighValue:
    def test_fires_above_threshold(self):
        tx = make_tx(amount_aed=Decimal("40001.00"))
        assert rule_high_value(tx) == AlertTrigger.HIGH_VALUE

    def test_fires_well_above_threshold(self):
        tx = make_tx(amount_aed=Decimal("250000.00"))
        assert rule_high_value(tx) == AlertTrigger.HIGH_VALUE

    def test_does_not_fire_below_threshold(self):
        tx = make_tx(amount_aed=Decimal("39999.99"))
        assert rule_high_value(tx) is None

    def test_does_not_fire_at_exact_threshold(self):
        tx = make_tx(amount_aed=Decimal("40000.00"))
        assert rule_high_value(tx) is None

    def test_fires_on_large_round_number(self):
        tx = make_tx(amount_aed=Decimal("100000.00"))
        assert rule_high_value(tx) == AlertTrigger.HIGH_VALUE


# ── Rule 2: Sanctioned corridor ───────────────────────────────────────────────


class TestRuleSanctionedCorridor:
    def test_fires_for_iran(self):
        tx = make_tx(country="IR")
        assert rule_sanctioned_corridor(tx) == AlertTrigger.SANCTIONED_CORRIDOR

    def test_fires_for_north_korea(self):
        tx = make_tx(country="KP")
        assert rule_sanctioned_corridor(tx) == AlertTrigger.SANCTIONED_CORRIDOR

    def test_fires_for_syria(self):
        tx = make_tx(country="SY")
        assert rule_sanctioned_corridor(tx) == AlertTrigger.SANCTIONED_CORRIDOR

    def test_fires_for_myanmar(self):
        tx = make_tx(country="MM")
        assert rule_sanctioned_corridor(tx) == AlertTrigger.SANCTIONED_CORRIDOR

    def test_does_not_fire_for_uae(self):
        tx = make_tx(country="AE")
        assert rule_sanctioned_corridor(tx) is None

    def test_does_not_fire_for_saudi(self):
        tx = make_tx(country="SA")
        assert rule_sanctioned_corridor(tx) is None

    def test_does_not_fire_for_uk(self):
        tx = make_tx(country="GB")
        assert rule_sanctioned_corridor(tx) is None

    def test_does_not_fire_for_usa(self):
        tx = make_tx(country="US")
        assert rule_sanctioned_corridor(tx) is None


# ── Rule 3: Device mismatch ───────────────────────────────────────────────────


class TestRuleDeviceMismatch:
    def test_fires_when_device_changed(self):
        tx = make_tx()
        profiles = {
            "CUST001": make_kyc(
                device_id="device-NEW-xyz",
                last_known_device="device-OLD-abc",
            )
        }
        assert rule_device_mismatch(tx, profiles) == AlertTrigger.DEVICE_MISMATCH

    def test_does_not_fire_when_devices_match(self):
        tx = make_tx()
        profiles = {
            "CUST001": make_kyc(
                device_id="device-same-111",
                last_known_device="device-same-111",
            )
        }
        assert rule_device_mismatch(tx, profiles) is None

    def test_does_not_fire_when_no_kyc_profile(self):
        tx = make_tx(customer_id="CUST999")
        profiles = {}  # customer not in system
        assert rule_device_mismatch(tx, profiles) is None

    def test_does_not_fire_for_different_customer(self):
        tx = make_tx(customer_id="CUST002")
        profiles = {
            "CUST001": make_kyc(  # different customer has mismatch
                device_id="device-NEW",
                last_known_device="device-OLD",
            )
        }
        assert rule_device_mismatch(tx, profiles) is None


# ── Rule 4: New account ───────────────────────────────────────────────────────


class TestRuleNewAccount:
    def test_fires_for_new_account_with_large_tx(self):
        tx = make_tx(amount_aed=Decimal("10000.00"))
        profiles = {"CUST001": make_kyc(account_age_days=10)}
        assert rule_new_account(tx, profiles) == AlertTrigger.NEW_ACCOUNT

    def test_does_not_fire_for_established_account(self):
        tx = make_tx(amount_aed=Decimal("10000.00"))
        profiles = {"CUST001": make_kyc(account_age_days=180)}
        assert rule_new_account(tx, profiles) is None

    def test_does_not_fire_for_new_account_small_tx(self):
        tx = make_tx(amount_aed=Decimal("500.00"))
        profiles = {"CUST001": make_kyc(account_age_days=5)}
        assert rule_new_account(tx, profiles) is None

    def test_boundary_30_days(self):
        tx = make_tx(amount_aed=Decimal("10000.00"))
        # Exactly 30 days = NOT new
        profiles = {"CUST001": make_kyc(account_age_days=30)}
        assert rule_new_account(tx, profiles) is None

        # 29 days = new account
        profiles = {"CUST001": make_kyc(account_age_days=29)}
        assert rule_new_account(tx, profiles) == AlertTrigger.NEW_ACCOUNT


# ── Alert engine integration tests ────────────────────────────────────────────


class TestAlertEngine:
    def test_engine_creates_no_alerts_for_clean_tx(self):
        engine = AlertEngine()
        tx = make_tx(
            amount_aed=Decimal("1000.00"),
            country="AE",
        )
        alerts = engine.evaluate(tx)
        assert len(alerts) == 0

    def test_engine_creates_alert_for_high_value(self):
        engine = AlertEngine()
        tx = make_tx(amount_aed=Decimal("50000.00"), country="AE")
        alerts = engine.evaluate(tx)
        triggers = [a.trigger for a in alerts]
        assert AlertTrigger.HIGH_VALUE in triggers

    def test_engine_creates_alert_for_sanctioned_country(self):
        engine = AlertEngine()
        tx = make_tx(amount_aed=Decimal("1000.00"), country="IR")
        alerts = engine.evaluate(tx)
        triggers = [a.trigger for a in alerts]
        assert AlertTrigger.SANCTIONED_CORRIDOR in triggers

    def test_engine_can_fire_multiple_rules_on_one_tx(self):
        """A single transaction can trigger both HIGH_VALUE and SANCTIONED_CORRIDOR."""
        engine = AlertEngine()
        tx = make_tx(
            amount_aed=Decimal("250000.00"),
            country="KP",
        )
        alerts = engine.evaluate(tx)
        assert len(alerts) >= 2
        triggers = [a.trigger for a in alerts]
        assert AlertTrigger.HIGH_VALUE in triggers
        assert AlertTrigger.SANCTIONED_CORRIDOR in triggers

    def test_engine_saves_alerts_to_store(self):
        engine = AlertEngine()
        tx = make_tx(amount_aed=Decimal("50000.00"), country="IR")
        before = store.count()
        engine.evaluate(tx)
        assert store.count() > before

    def test_engine_logs_audit_event(self):
        engine = AlertEngine()
        tx = make_tx(amount_aed=Decimal("50000.00"), country="AE")
        alerts = engine.evaluate(tx)
        assert len(alerts) > 0
        events = store.get_audit_trail(str(alerts[0].alert_id))
        assert len(events) == 1
        assert events[0].event_type == "ALERT_CREATED"
        assert events[0].actor == "alert_engine"

    def test_batch_evaluation(self):
        engine = AlertEngine()
        txs = [
            make_tx(tx_id="TX-001", amount_aed=Decimal("1000"), country="AE"),
            make_tx(tx_id="TX-002", amount_aed=Decimal("50000"), country="AE"),
            make_tx(tx_id="TX-003", amount_aed=Decimal("1000"), country="IR"),
        ]
        alerts = engine.evaluate_batch(txs)
        # TX-001: no alerts, TX-002: HIGH_VALUE, TX-003: SANCTIONED
        assert len(alerts) == 2

"""
tests/test_models.py
─────────────────────
Unit tests for all Pydantic data models (app/shared/models.py).

Run with:
    uv run pytest tests/test_models.py -v
"""

from decimal import Decimal
from datetime import datetime

import pytest

from app.shared.models import (
    Alert,
    AlertCreateRequest,
    AlertResponse,
    AlertStatus,
    AlertTrigger,
    AuditEvent,
    HITLDecisionRequest,
    KYCProfile,
    RiskBand,
    SanctionsEntry,
    Transaction,
    Verdict,
)


# ── Transaction tests ────────────────────────────────────────────────────────


class TestTransaction:
    def test_valid_transaction(self):
        tx = Transaction(
            tx_id="TX-001",
            customer_id="CUST001",
            amount_aed=Decimal("15000.00"),
            merchant="Dubai Mall Retail",
            country="AE",
            timestamp=datetime.utcnow(),
        )
        assert tx.tx_id == "TX-001"
        assert tx.currency == "AED"
        assert tx.is_flagged is False

    def test_country_is_uppercased(self):
        tx = Transaction(
            tx_id="TX-002",
            customer_id="CUST001",
            amount_aed=Decimal("5000"),
            merchant="Test Merchant",
            country="ae",  # lowercase input
            timestamp=datetime.utcnow(),
        )
        assert tx.country == "AE"  # should be uppercased

    def test_currency_is_uppercased(self):
        tx = Transaction(
            tx_id="TX-003",
            customer_id="CUST001",
            amount_aed=Decimal("5000"),
            currency="usd",
            merchant="Test",
            country="US",
            timestamp=datetime.utcnow(),
        )
        assert tx.currency == "USD"

    def test_amount_must_be_positive(self):
        with pytest.raises(ValueError):
            Transaction(
                tx_id="TX-BAD",
                customer_id="CUST001",
                amount_aed=Decimal("-100"),
                merchant="Test",
                country="AE",
                timestamp=datetime.utcnow(),
            )

    def test_zero_amount_rejected(self):
        with pytest.raises(ValueError):
            Transaction(
                tx_id="TX-ZERO",
                customer_id="CUST001",
                amount_aed=Decimal("0"),
                merchant="Test",
                country="AE",
                timestamp=datetime.utcnow(),
            )

    def test_amount_float_property(self):
        tx = Transaction(
            tx_id="TX-004",
            customer_id="CUST001",
            amount_aed=Decimal("12345.67"),
            merchant="Test",
            country="AE",
            timestamp=datetime.utcnow(),
        )
        assert tx.amount_float == 12345.67
        assert isinstance(tx.amount_float, float)

    def test_flagged_transaction(self):
        tx = Transaction(
            tx_id="TX-005",
            customer_id="CUST001",
            amount_aed=Decimal("250000"),
            merchant="Shell Holdings Ltd",
            country="IR",
            timestamp=datetime.utcnow(),
            is_flagged=True,
        )
        assert tx.is_flagged is True
        assert tx.country == "IR"


# ── KYCProfile tests ─────────────────────────────────────────────────────────


class TestKYCProfile:
    def _make_profile(self, **kwargs) -> KYCProfile:
        defaults = {
            "customer_id": "CUST001",
            "name": "Ahmed Al Mansoori",
            "nationality": "AE",
            "account_age_days": 365,
            "device_id": "device-aaa-111",
            "last_known_device": "device-aaa-111",
            "risk_tier": RiskBand.LOW,
        }
        defaults.update(kwargs)
        return KYCProfile(**defaults)

    def test_valid_profile(self):
        profile = self._make_profile()
        assert profile.customer_id == "CUST001"
        assert profile.risk_tier == RiskBand.LOW

    def test_no_device_mismatch_when_same(self):
        profile = self._make_profile(
            device_id="device-abc",
            last_known_device="device-abc",
        )
        assert profile.has_device_mismatch is False

    def test_device_mismatch_detected(self):
        profile = self._make_profile(
            device_id="device-new-xyz",
            last_known_device="device-old-abc",
        )
        assert profile.has_device_mismatch is True

    def test_new_account_flag(self):
        new_profile = self._make_profile(account_age_days=15)
        assert new_profile.is_new_account is True

    def test_established_account_flag(self):
        old_profile = self._make_profile(account_age_days=365)
        assert old_profile.is_new_account is False

    def test_new_account_boundary(self):
        # Exactly 30 days = NOT new
        profile = self._make_profile(account_age_days=30)
        assert profile.is_new_account is False

        # 29 days = new account
        profile = self._make_profile(account_age_days=29)
        assert profile.is_new_account is True


# ── Alert tests ───────────────────────────────────────────────────────────────


class TestAlert:
    def test_alert_created_with_defaults(self):
        alert = Alert(
            tx_id="TX-001",
            customer_id="CUST001",
            trigger=AlertTrigger.HIGH_VALUE,
        )
        assert alert.status == AlertStatus.PENDING
        assert alert.risk_score is None
        assert alert.risk_band is None
        assert alert.alert_id is not None

    def test_alert_id_is_unique(self):
        a1 = Alert(tx_id="TX-001", customer_id="CUST001", trigger=AlertTrigger.MANUAL)
        a2 = Alert(tx_id="TX-001", customer_id="CUST001", trigger=AlertTrigger.MANUAL)
        assert a1.alert_id != a2.alert_id

    def test_alert_response_from_alert(self):
        alert = Alert(
            tx_id="TX-001",
            customer_id="CUST001",
            trigger=AlertTrigger.SANCTIONED_CORRIDOR,
            risk_score=85,
            risk_band=RiskBand.HIGH,
        )
        response = AlertResponse.from_alert(alert)
        assert response.alert_id == str(alert.alert_id)
        assert response.risk_score == 85
        assert response.risk_band == RiskBand.HIGH


# ── HITL decision tests ───────────────────────────────────────────────────────


class TestHITLDecision:
    def test_valid_decision(self):
        decision = HITLDecisionRequest(
            verdict=Verdict.FRAUD_CONFIRMED,
            analyst_notes="Clear sanctions evasion pattern — confirmed fraud.",
            analyst_id="analyst-01",
        )
        assert decision.verdict == Verdict.FRAUD_CONFIRMED

    def test_notes_too_short_rejected(self):
        with pytest.raises(ValueError):
            HITLDecisionRequest(
                verdict=Verdict.FALSE_POSITIVE,
                analyst_notes="ok",  # too short — min 10 chars
                analyst_id="analyst-01",
            )

    def test_false_positive_verdict(self):
        decision = HITLDecisionRequest(
            verdict=Verdict.FALSE_POSITIVE,
            analyst_notes="Customer is a known corporate — legitimate large transfer.",
            analyst_id="analyst-02",
        )
        assert decision.verdict == Verdict.FALSE_POSITIVE


# ── Sanctions tests ───────────────────────────────────────────────────────────


class TestSanctionsEntry:
    def test_sanctions_entry_with_aliases(self):
        entry = SanctionsEntry(
            name="Mohamed Al Rashid Trading",
            aliases=["Mohammed Al Rashid", "Muhammad Rashid Trading"],
            country="YE",
            reason="OFAC — terrorism financing",
        )
        assert len(entry.aliases) == 2
        assert entry.country == "YE"

    def test_sanctions_entry_no_aliases(self):
        entry = SanctionsEntry(
            name="Eastern Star Corp",
            country="KP",
            reason="UN Security Council",
        )
        assert entry.aliases == []


# ── Config tests ──────────────────────────────────────────────────────────────


class TestSettings:
    def test_risk_band_label_low(self):
        from app.core.config import Settings
        s = Settings(gemini_api_key="", groq_api_key="")
        assert s.risk_band_label(0) == "LOW"
        assert s.risk_band_label(29) == "LOW"

    def test_risk_band_label_medium(self):
        from app.core.config import Settings
        s = Settings(gemini_api_key="", groq_api_key="")
        assert s.risk_band_label(30) == "MEDIUM"
        assert s.risk_band_label(69) == "MEDIUM"

    def test_risk_band_label_high(self):
        from app.core.config import Settings
        s = Settings(gemini_api_key="", groq_api_key="")
        assert s.risk_band_label(70) == "HIGH"
        assert s.risk_band_label(89) == "HIGH"

    def test_risk_band_label_critical(self):
        from app.core.config import Settings
        s = Settings(gemini_api_key="", groq_api_key="")
        assert s.risk_band_label(90) == "CRITICAL"
        assert s.risk_band_label(100) == "CRITICAL"

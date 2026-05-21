"""
tests/test_triage.py
─────────────────────
Phase 3 triage pipeline tests.

All LLM calls are mocked — tests run in milliseconds,
cost $0 in API credits, and are fully deterministic.

Coverage:
  - TriageResult Pydantic validation (valid and invalid inputs)
  - Prompt building with all context combinations
  - TriageService state transitions (all 3 outcomes)
  - TriageService failure handling (alert reverts to PENDING)
  - All 4 triage API endpoints

Run with:
    uv run pytest tests/test_triage.py -v
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.llm.client import LLMResponse
from app.llm.prompts import TRIAGE_SYSTEM_PROMPT, build_triage_prompt
from app.main import app
from app.services.alert_store import store
from app.services.triage_service import TriageResult, TriageService
from app.shared.models import (
    Alert, AlertStatus, AlertTrigger,
    KYCProfile, RiskBand, Transaction,
)

client = TestClient(app)


# ── Test data ─────────────────────────────────────────────────────────────────

VALID_HIGH_RISK = {
    "severity_score"    : 75,
    "severity_band"     : "HIGH",
    "initial_suspicion" : "Large transfer to sanctioned corridor with device mismatch.",
    "risk_factors"      : [
        "AED 250,000 exceeds CBUAE reporting threshold",
        "Country IR under OFAC/UN sanctions",
        "Device fingerprint mismatch — possible account takeover",
    ],
    "recommended_action": "INVESTIGATE",
    "regulatory_flags"  : ["CBUAE STR required if confirmed"],
    "confidence"        : "HIGH",
}

VALID_LOW_RISK = {
    "severity_score"    : 15,
    "severity_band"     : "LOW",
    "initial_suspicion" : "Small UAE transaction — no elevated risk indicators.",
    "risk_factors"      : ["Amount within normal range"],
    "recommended_action": "AUTO_CLOSE",
    "regulatory_flags"  : [],
    "confidence"        : "HIGH",
}

VALID_CRITICAL = {
    "severity_score"    : 95,
    "severity_band"     : "CRITICAL",
    "initial_suspicion" : "Multiple critical fraud indicators — immediate action required.",
    "risk_factors"      : ["OFAC SDN match", "New account", "Device mismatch"],
    "recommended_action": "ESCALATE_IMMEDIATELY",
    "regulatory_flags"  : ["CBUAE STR mandatory"],
    "confidence"        : "HIGH",
}


def mock_llm(data: dict) -> LLMResponse:
    return LLMResponse(
        content    = json.dumps(data),
        provider   = "gemini",
        latency_ms = 450.0,
        model      = "gemini-test",
    )


def make_alert(**kw) -> Alert:
    return Alert(
        tx_id      = kw.get("tx_id", "TX-001"),
        customer_id= kw.get("customer_id", "CUST001"),
        trigger    = kw.get("trigger", AlertTrigger.HIGH_VALUE),
    )


def make_tx(**kw) -> Transaction:
    return Transaction(
        tx_id      = kw.get("tx_id", "TX-001"),
        customer_id= kw.get("customer_id", "CUST001"),
        amount_aed = Decimal(str(kw.get("amount_aed", "250000"))),
        currency   = "AED",
        merchant   = kw.get("merchant", "Test Merchant"),
        country    = kw.get("country", "IR"),
        timestamp  = datetime.utcnow(),
    )


def make_kyc(**kw) -> KYCProfile:
    return KYCProfile(
        customer_id      = kw.get("customer_id", "CUST001"),
        name             = "Test User",
        nationality      = "AE",
        account_age_days = kw.get("account_age_days", 365),
        device_id        = kw.get("device_id", "dev-abc"),
        last_known_device= kw.get("last_known_device", "dev-abc"),
        risk_tier        = RiskBand.LOW,
    )


@pytest.fixture(autouse=True)
def clear():
    store.clear()
    yield
    store.clear()


# ── TriageResult validation ───────────────────────────────────────────────────

class TestTriageResult:
    def test_valid_high_risk(self):
        r = TriageResult(**VALID_HIGH_RISK)
        assert r.severity_score == 75
        assert r.severity_band  == RiskBand.HIGH
        assert r.recommended_action == "INVESTIGATE"

    def test_valid_low_risk(self):
        r = TriageResult(**VALID_LOW_RISK)
        assert r.recommended_action == "AUTO_CLOSE"

    def test_valid_critical(self):
        r = TriageResult(**VALID_CRITICAL)
        assert r.recommended_action == "ESCALATE_IMMEDIATELY"

    def test_score_above_100_rejected(self):
        with pytest.raises(Exception):
            TriageResult(**{**VALID_HIGH_RISK, "severity_score": 101})

    def test_negative_score_rejected(self):
        with pytest.raises(Exception):
            TriageResult(**{**VALID_HIGH_RISK, "severity_score": -1})

    def test_invalid_action_rejected(self):
        with pytest.raises(Exception):
            TriageResult(**{**VALID_HIGH_RISK, "recommended_action": "IGNORE"})

    def test_invalid_confidence_rejected(self):
        with pytest.raises(Exception):
            TriageResult(**{**VALID_HIGH_RISK, "confidence": "VERY_HIGH"})

    def test_empty_risk_factors_rejected(self):
        with pytest.raises(Exception):
            TriageResult(**{**VALID_HIGH_RISK, "risk_factors": []})

    def test_band_coercion_lowercase(self):
        r = TriageResult(**{**VALID_HIGH_RISK, "severity_band": "high"})
        assert r.severity_band == RiskBand.HIGH


# ── Prompt building ───────────────────────────────────────────────────────────

class TestPrompts:
    def test_prompt_contains_alert_id(self):
        alert  = make_alert()
        prompt = build_triage_prompt(alert)
        assert str(alert.alert_id) in prompt

    def test_prompt_contains_trigger(self):
        alert  = make_alert(trigger=AlertTrigger.SANCTIONED_CORRIDOR)
        prompt = build_triage_prompt(alert)
        assert "SANCTIONED_CORRIDOR" in prompt

    def test_prompt_with_transaction_contains_amount(self):
        alert  = make_alert()
        tx     = make_tx(amount_aed="250000")
        prompt = build_triage_prompt(alert, transaction=tx)
        assert "250,000.00" in prompt

    def test_high_value_adds_regulatory_note(self):
        alert  = make_alert()
        tx     = make_tx(amount_aed="50000")
        prompt = build_triage_prompt(alert, transaction=tx)
        assert "CBUAE reporting threshold" in prompt

    def test_no_transaction_says_not_available(self):
        alert  = make_alert()
        prompt = build_triage_prompt(alert, transaction=None)
        assert "Not available" in prompt

    def test_device_mismatch_adds_warning(self):
        alert  = make_alert()
        tx     = make_tx()
        kyc    = make_kyc(device_id="new", last_known_device="old")
        prompt = build_triage_prompt(alert, transaction=tx, kyc_profile=kyc)
        assert "account takeover" in prompt.lower()

    def test_new_account_adds_warning(self):
        alert  = make_alert()
        tx     = make_tx()
        kyc    = make_kyc(account_age_days=10)
        prompt = build_triage_prompt(alert, transaction=tx, kyc_profile=kyc)
        assert "30 days" in prompt

    def test_system_prompt_has_json_schema(self):
        assert "severity_score" in TRIAGE_SYSTEM_PROMPT
        assert "AUTO_CLOSE"     in TRIAGE_SYSTEM_PROMPT
        assert "CBUAE"          in TRIAGE_SYSTEM_PROMPT


# ── TriageService state transitions ──────────────────────────────────────────

class TestTriageService:
    @patch("app.services.triage_service.get_llm_client")
    def _run(self, data, mock_get):
        mc = MagicMock()
        mc.available_providers = ["gemini"]
        mc.complete.return_value = mock_llm(data)
        mock_get.return_value = mc
        alert = make_alert()
        store.save(alert)
        svc    = TriageService()
        result = svc.triage(alert)
        return result, store.get(str(alert.alert_id))

    def test_high_risk_becomes_investigating(self):
        result, updated = self._run(VALID_HIGH_RISK)
        assert result is not None
        assert updated.status == AlertStatus.INVESTIGATING
        assert updated.risk_score == 75

    def test_low_risk_becomes_auto_closed(self):
        _, updated = self._run(VALID_LOW_RISK)
        assert updated.status == AlertStatus.AUTO_CLOSED

    def test_critical_becomes_awaiting_human(self):
        _, updated = self._run(VALID_CRITICAL)
        assert updated.status == AlertStatus.AWAITING_HUMAN

    def test_triage_narrative_populated(self):
        _, updated = self._run(VALID_HIGH_RISK)
        assert updated.triage_narrative is not None
        assert len(updated.triage_narrative) > 20

    def test_audit_events_logged(self):
        @patch("app.services.triage_service.get_llm_client")
        def run(mock_get):
            mc = MagicMock()
            mc.available_providers = ["gemini"]
            mc.complete.return_value = mock_llm(VALID_HIGH_RISK)
            mock_get.return_value = mc
            alert = make_alert()
            store.save(alert)
            TriageService().triage(alert)
            return store.get_audit_trail(str(alert.alert_id))
        events = run()
        types = [e.event_type for e in events]
        assert "TRIAGE_STARTED"  in types
        assert "TRIAGE_COMPLETE" in types

    @patch("app.services.triage_service.get_llm_client")
    def test_skips_non_pending(self, mock_get):
        mc = MagicMock()
        mc.available_providers = ["gemini"]
        mock_get.return_value = mc
        alert = make_alert()
        alert.status = AlertStatus.INVESTIGATING
        store.save(alert)
        result = TriageService().triage(alert)
        assert result is None
        mc.complete.assert_not_called()

    @patch("app.services.triage_service.get_llm_client")
    def test_failure_reverts_to_pending(self, mock_get):
        mc = MagicMock()
        mc.available_providers = ["gemini"]
        mc.complete.side_effect = RuntimeError("Timeout")
        mock_get.return_value = mc
        alert = make_alert()
        store.save(alert)
        result  = TriageService().triage(alert)
        updated = store.get(str(alert.alert_id))
        assert result is None
        assert updated.status == AlertStatus.PENDING


# ── API endpoint tests ────────────────────────────────────────────────────────

class TestTriageAPI:
    def test_batch_no_pending_returns_400(self):
        r = client.post("/v1/triage/batch", json={"max_alerts": 5})
        assert r.status_code == 400

    def test_single_not_found_returns_404(self):
        r = client.post("/v1/triage/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404

    def test_single_non_pending_returns_400(self):
        alert = make_alert()
        alert.status = AlertStatus.INVESTIGATING
        store.save(alert)
        r = client.post(f"/v1/triage/{alert.alert_id}")
        assert r.status_code == 400
        assert "PENDING" in r.json()["detail"]

    def test_stats_structure(self):
        r = client.get("/v1/triage/stats")
        assert r.status_code == 200
        d = r.json()
        assert "total_alerts"   in d
        assert "PENDING"        in d
        assert "AUTO_CLOSED"    in d
        assert "average_risk_score" in d

    def test_get_result_not_triaged_returns_400(self):
        alert = make_alert()
        store.save(alert)
        r = client.get(f"/v1/triage/{alert.alert_id}")
        assert r.status_code == 400
        assert "not been triaged" in r.json()["detail"]

"""
tests/test_hitl.py
───────────────────
Tests for Phase 5 HITL review and fraud memory.

Fix: patch only app.services.fraud_memory.MEMORY_PATH — that is the
single location where the path is defined and used. hitl_service.py
imports functions from fraud_memory.py at call time, so patching
the source module is sufficient for all callers.

Run:
    uv run pytest tests/test_hitl.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.alert_store import store
from app.shared.models import Alert, AlertStatus, AlertTrigger

client = TestClient(app)

MEMORY_MODULE = "app.services.fraud_memory.MEMORY_PATH"


@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    """
    Clear alert store and redirect fraud_memory writes to a tmp file.
    Only app.services.fraud_memory.MEMORY_PATH needs patching — it is
    the single source of truth for the path. hitl_service and the API
    both import functions from fraud_memory, not the path constant.
    """
    store.clear()
    memory_file = tmp_path / "fraud_memory.json"
    with patch(MEMORY_MODULE, memory_file):
        yield
    store.clear()


def make_alert(status: AlertStatus = AlertStatus.AWAITING_HUMAN) -> Alert:
    a = Alert(
        tx_id       = "TX-HITL-001",
        customer_id = "CUST001",
        trigger     = AlertTrigger.HIGH_VALUE,
        status      = status,
    )
    a.risk_score       = 82
    a.triage_narrative = "High-value transfer — investigation required."
    store.save(a)
    return a


# ── Fraud memory tests ────────────────────────────────────────────────────────

class TestFraudMemory:
    def test_record_and_retrieve(self):
        from app.services.fraud_memory import record_outcome, retrieve_similar_cases
        record_outcome(
            alert_id="alert-001", tx_id="TX-001",
            customer_id="CUST001", trigger="HIGH_VALUE",
            country="IR", merchant="Test Corp",
            risk_score=85, verdict="CONFIRMED_FRAUD",
            analyst="analyst@bank.ae",
            analyst_notes="Clear sanctions hit confirmed by compliance team.",
            risk_signals=["High value", "FATF country"],
        )
        cases = retrieve_similar_cases(customer_id="CUST001", max_results=3)
        assert len(cases) == 1
        assert cases[0]["verdict"]     == "CONFIRMED_FRAUD"
        assert cases[0]["customer_id"] == "CUST001"

    def test_similar_cases_by_country(self):
        from app.services.fraud_memory import record_outcome, retrieve_similar_cases
        record_outcome(
            alert_id="alert-002", tx_id="TX-002",
            customer_id="CUST999", trigger="SANCTIONED_CORRIDOR",
            country="IR", merchant="Test Corp",
            risk_score=90, verdict="CONFIRMED_FRAUD",
            analyst="analyst@bank.ae",
            analyst_notes="Sanctions corridor confirmed — OFAC SDN match.",
            risk_signals=["Sanctioned corridor"],
        )
        cases = retrieve_similar_cases(
            customer_id="CUST001",  # different customer
            country="IR",           # same country — still matches
            max_results=3,
        )
        assert len(cases) == 1
        assert cases[0]["verdict"] == "CONFIRMED_FRAUD"

    def test_no_similar_cases_returns_empty(self):
        from app.services.fraud_memory import retrieve_similar_cases
        assert retrieve_similar_cases(customer_id="CUST999") == []

    def test_memory_stats_empty(self):
        from app.services.fraud_memory import get_memory_stats
        stats = get_memory_stats()
        assert stats["total_cases"]     == 0
        assert stats["confirmed_fraud"] == 0
        assert stats["false_positives"] == 0

    def test_memory_stats_after_recording(self):
        from app.services.fraud_memory import record_outcome, get_memory_stats
        record_outcome(
            alert_id="a1", tx_id="t1", customer_id="C1",
            trigger="HIGH_VALUE", country="IR", merchant="M",
            risk_score=85, verdict="CONFIRMED_FRAUD",
            analyst="a@b.ae",
            analyst_notes="Confirmed — clear OFAC SDN match evidence.",
            risk_signals=[],
        )
        record_outcome(
            alert_id="a2", tx_id="t2", customer_id="C2",
            trigger="HIGH_VALUE", country="AE", merchant="M",
            risk_score=30, verdict="FALSE_POSITIVE",
            analyst="a@b.ae",
            analyst_notes="False positive — verified by relationship manager.",
            risk_signals=[],
        )
        stats = get_memory_stats()
        assert stats["total_cases"]     == 2
        assert stats["confirmed_fraud"] == 1
        assert stats["false_positives"] == 1

    def test_max_results_respected(self):
        from app.services.fraud_memory import record_outcome, retrieve_similar_cases
        for i in range(5):
            record_outcome(
                alert_id=f"alert-{i}", tx_id=f"TX-{i}",
                customer_id="CUST001", trigger="HIGH_VALUE",
                country="IR", merchant="M", risk_score=70,
                verdict="CONFIRMED_FRAUD", analyst="a@b.ae",
                analyst_notes=f"Case {i} confirmed fraud — multiple signals.",
                risk_signals=[],
            )
        cases = retrieve_similar_cases("CUST001", max_results=2)
        assert len(cases) <= 2


# ── HITL service tests ────────────────────────────────────────────────────────

class TestHITLService:
    def test_confirmed_fraud_moves_to_fraud_confirmed(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        result  = service.process_decision(
            alert_id = str(alert.alert_id),
            verdict  = "CONFIRMED_FRAUD",
            analyst  = "senior.analyst@bank.ae",
            notes    = "Multiple sanctions hits confirmed. STR to be filed.",
        )
        assert result.verdict     == "CONFIRMED_FRAUD"
        assert result.new_status  == "FRAUD_CONFIRMED"
        assert result.str_required is True
        updated = store.get(str(alert.alert_id))
        assert updated.status == AlertStatus.FRAUD_CONFIRMED

    def test_false_positive_moves_to_false_positive(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        result  = service.process_decision(
            alert_id = str(alert.alert_id),
            verdict  = "FALSE_POSITIVE",
            analyst  = "analyst@bank.ae",
            notes    = "Legitimate business transfer — customer verified by relationship manager.",
        )
        assert result.verdict     == "FALSE_POSITIVE"
        assert result.new_status  == "FALSE_POSITIVE"
        assert result.str_required is False

    def test_escalated_moves_to_awaiting_human(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert(status=AlertStatus.INVESTIGATING)
        service = HITLService()
        result  = service.process_decision(
            alert_id = str(alert.alert_id),
            verdict  = "ESCALATED",
            analyst  = "analyst@bank.ae",
            notes    = "Needs senior compliance officer review — complex cross-border pattern.",
        )
        assert result.new_status == "AWAITING_HUMAN"

    def test_invalid_verdict_raises(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        with pytest.raises(ValueError, match="Invalid verdict"):
            service.process_decision(
                alert_id = str(alert.alert_id),
                verdict  = "INVALID_OPTION",
                analyst  = "analyst@bank.ae",
                notes    = "This should fail immediately.",
            )

    def test_notes_too_short_raises(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        with pytest.raises(ValueError, match="20 characters"):
            service.process_decision(
                alert_id = str(alert.alert_id),
                verdict  = "CONFIRMED_FRAUD",
                analyst  = "analyst@bank.ae",
                notes    = "Too short",
            )

    def test_unknown_alert_raises(self):
        from app.services.hitl_service import HITLService
        service = HITLService()
        with pytest.raises(ValueError, match="not found"):
            service.process_decision(
                alert_id = "00000000-0000-0000-0000-000000000000",
                verdict  = "FALSE_POSITIVE",
                analyst  = "analyst@bank.ae",
                notes    = "This alert does not exist in the store.",
            )

    def test_wrong_status_raises(self):
        from app.services.hitl_service import HITLService
        alert        = make_alert()
        alert.status = AlertStatus.AUTO_CLOSED
        store.save(alert)
        service = HITLService()
        with pytest.raises(ValueError, match="AUTO_CLOSED"):
            service.process_decision(
                alert_id = str(alert.alert_id),
                verdict  = "FALSE_POSITIVE",
                analyst  = "analyst@bank.ae",
                notes    = "Should fail because alert is already closed.",
            )

    def test_audit_event_logged(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        service.process_decision(
            alert_id = str(alert.alert_id),
            verdict  = "FALSE_POSITIVE",
            analyst  = "analyst@bank.ae",
            notes    = "Verified legitimate transaction with relationship manager confirmation.",
        )
        events = store.get_audit_trail(str(alert.alert_id))
        types  = [e.event_type for e in events]
        assert "HITL_DECISION" in types

    def test_memory_recorded_after_decision(self):
        from app.services.hitl_service import HITLService
        from app.services.fraud_memory import retrieve_similar_cases
        alert   = make_alert()
        service = HITLService()
        service.process_decision(
            alert_id = str(alert.alert_id),
            verdict  = "CONFIRMED_FRAUD",
            analyst  = "analyst@bank.ae",
            notes    = "OFAC SDN match confirmed by compliance team review.",
        )
        cases = retrieve_similar_cases(customer_id="CUST001")
        assert len(cases) == 1
        assert cases[0]["verdict"] == "CONFIRMED_FRAUD"

    def test_get_review_context_returns_full_package(self):
        from app.services.hitl_service import HITLService
        alert   = make_alert()
        service = HITLService()
        context = service.get_review_context(str(alert.alert_id))
        assert "alert"               in context
        assert "similar_past_cases"  in context
        assert "regulatory_guidance" in context
        assert "valid_verdicts"      in context
        assert "audit_trail"         in context
        assert context["alert"]["alert_id"] == str(alert.alert_id)


# ── HITL API endpoint tests ───────────────────────────────────────────────────

class TestHITLAPI:
    def test_queue_returns_200(self):
        r = client.get("/v1/hitl/queue")
        assert r.status_code == 200
        assert "queue_length" in r.json()
        assert "alerts"       in r.json()

    def test_queue_contains_awaiting_human(self):
        make_alert(status=AlertStatus.AWAITING_HUMAN)
        r    = client.get("/v1/hitl/queue")
        data = r.json()
        assert data["queue_length"] == 1
        assert data["alerts"][0]["trigger"] == "HIGH_VALUE"

    def test_queue_excludes_other_statuses(self):
        make_alert(status=AlertStatus.AUTO_CLOSED)
        r = client.get("/v1/hitl/queue")
        assert r.json()["queue_length"] == 0

    def test_context_returns_200_for_valid_alert(self):
        alert = make_alert()
        r     = client.get(f"/v1/hitl/{alert.alert_id}/context")
        assert r.status_code == 200
        assert "alert"               in r.json()
        assert "regulatory_guidance" in r.json()

    def test_context_404_for_unknown_alert(self):
        r = client.get("/v1/hitl/00000000-0000-0000-0000-000000000000/context")
        assert r.status_code == 404

    def test_decision_confirmed_fraud(self):
        alert = make_alert()
        r     = client.post(
            f"/v1/hitl/{alert.alert_id}/decision",
            json={
                "verdict" : "CONFIRMED_FRAUD",
                "analyst" : "senior.analyst@bank.ae",
                "notes"   : "Multiple signals confirmed by compliance team. STR to be filed.",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"]      == "CONFIRMED_FRAUD"
        assert data["str_required"] is True
        assert data["memory_id"]    != ""

    def test_decision_false_positive(self):
        alert = make_alert()
        r     = client.post(
            f"/v1/hitl/{alert.alert_id}/decision",
            json={
                "verdict": "FALSE_POSITIVE",
                "analyst": "analyst@bank.ae",
                "notes"  : "Verified with relationship manager — legitimate business payment.",
            },
        )
        assert r.status_code == 200
        assert r.json()["verdict"]      == "FALSE_POSITIVE"
        assert r.json()["str_required"] is False

    def test_decision_invalid_verdict_returns_400(self):
        alert = make_alert()
        r     = client.post(
            f"/v1/hitl/{alert.alert_id}/decision",
            json={
                "verdict": "NOT_A_VALID_VERDICT",
                "analyst": "analyst@bank.ae",
                "notes"  : "This should fail validation.",
            },
        )
        assert r.status_code in (400, 422)

    def test_decision_short_notes_returns_400(self):
        alert = make_alert()
        r     = client.post(
            f"/v1/hitl/{alert.alert_id}/decision",
            json={
                "verdict": "FALSE_POSITIVE",
                "analyst": "analyst@bank.ae",
                "notes"  : "Too short",
            },
        )
        assert r.status_code in (400, 422)


    def test_memory_stats_endpoint(self):
        r = client.get("/v1/hitl/memory/stats")
        assert r.status_code == 200
        assert "total_cases"     in r.json()
        assert "confirmed_fraud" in r.json()

    def test_memory_cases_endpoint(self):
        r = client.get("/v1/hitl/memory/cases")
        assert r.status_code == 200
        assert "total" in r.json()
        assert "cases" in r.json()

"""
tests/test_alerts_api.py
─────────────────────────
Integration tests for the /v1/alerts API endpoints.

All tests that call POST /v1/alerts/generate mock the data loader
so tests are independent of the filesystem and work on any machine
without requiring transaction data files to be present.

Run with:
    uv run pytest tests/test_alerts_api.py -v
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.alert_store import store
from app.shared.models import Transaction

client = TestClient(app)

# ── Shared mock transaction ───────────────────────────────────────────────────
# Used by all tests that need the generate endpoint to produce alerts.
# A high-value IR transaction fires both HIGH_VALUE and SANCTIONED_CORRIDOR.

MOCK_TRANSACTIONS = {
    "TX-001": Transaction(
        tx_id       = "TX-001",
        customer_id = "CUST001",
        amount_aed  = Decimal("50000.00"),
        currency    = "AED",
        merchant    = "Test Merchant",
        country     = "IR",
        timestamp   = datetime.utcnow(),
        is_flagged  = True,
    )
}


@pytest.fixture(autouse=True)
def clear_store_between_tests():
    store.clear()
    yield
    store.clear()


# ── Generate alerts endpoint ──────────────────────────────────────────────────

class TestGenerateAlerts:
    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_generate_returns_200(self, _):
        response = client.post("/v1/alerts/generate", json={"limit": 5})
        assert response.status_code == 200

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_generate_response_schema(self, _):
        response = client.post("/v1/alerts/generate", json={"limit": 5})
        data = response.json()
        assert "alerts_created"  in data
        assert "alert_ids"       in data
        assert "message"         in data
        assert "data_source"     in data

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_generate_creates_alerts_in_store(self, _):
        client.post("/v1/alerts/generate", json={"limit": 5})
        assert store.count() > 0

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_generate_alert_ids_are_strings(self, _):
        response = client.post("/v1/alerts/generate", json={"limit": 5})
        data = response.json()
        for aid in data["alert_ids"]:
            assert isinstance(aid, str)
            assert len(aid) == 36  # UUID format

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_generate_flagged_only(self, _):
        response = client.post(
            "/v1/alerts/generate",
            json={"limit": 50, "flagged_only": True},
        )
        assert response.status_code == 200


# ── List alerts endpoint ──────────────────────────────────────────────────────

class TestListAlerts:
    def test_list_returns_empty_when_no_alerts(self):
        response = client.get("/v1/alerts")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["alerts"] == []

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_list_returns_alerts_after_generate(self, _):
        client.post("/v1/alerts/generate", json={"limit": 10})
        response = client.get("/v1/alerts")
        data = response.json()
        assert data["total"] > 0
        assert len(data["alerts"]) > 0

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_list_alert_schema(self, _):
        client.post("/v1/alerts/generate", json={"limit": 5})
        response = client.get("/v1/alerts")
        alerts = response.json()["alerts"]
        if alerts:
            a = alerts[0]
            assert "alert_id"    in a
            assert "tx_id"       in a
            assert "customer_id" in a
            assert "trigger"     in a
            assert "status"      in a
            assert "created_at"  in a

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_list_filter_by_status(self, _):
        client.post("/v1/alerts/generate", json={"limit": 10})
        response = client.get("/v1/alerts?status=PENDING")
        assert response.status_code == 200
        data = response.json()
        for alert in data["alerts"]:
            assert alert["status"] == "PENDING"

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_list_filter_by_trigger(self, _):
        client.post("/v1/alerts/generate", json={"limit": 20})
        response = client.get("/v1/alerts?trigger=HIGH_VALUE")
        assert response.status_code == 200
        data = response.json()
        for alert in data["alerts"]:
            assert alert["trigger"] == "HIGH_VALUE"


# ── Get single alert endpoint ─────────────────────────────────────────────────

class TestGetAlert:
    def test_get_returns_404_for_unknown_id(self):
        response = client.get("/v1/alerts/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_get_returns_alert_after_generate(self, _):
        gen = client.post("/v1/alerts/generate", json={"limit": 5}).json()
        if gen["alert_ids"]:
            alert_id = gen["alert_ids"][0]
            response = client.get(f"/v1/alerts/{alert_id}")
            assert response.status_code == 200
            assert response.json()["alert_id"] == alert_id


# ── Stats endpoint ────────────────────────────────────────────────────────────

class TestAlertStats:
    def test_stats_returns_zeros_when_empty(self):
        response = client.get("/v1/alerts/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_stats_reflects_generated_alerts(self, _):
        client.post("/v1/alerts/generate", json={"limit": 10})
        response = client.get("/v1/alerts/stats")
        data = response.json()
        assert data["total"] > 0
        assert "by_status"    in data
        assert "by_risk_band" in data

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_stats_pending_count_matches_list(self, _):
        client.post("/v1/alerts/generate", json={"limit": 10})
        stats     = client.get("/v1/alerts/stats").json()
        list_resp = client.get("/v1/alerts?status=PENDING").json()
        assert stats["by_status"]["PENDING"] == len(list_resp["alerts"])


# ── Audit trail endpoint ──────────────────────────────────────────────────────

class TestAuditTrail:
    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_audit_trail_has_events_after_create(self, _):
        gen = client.post("/v1/alerts/generate", json={"limit": 5}).json()
        if gen["alert_ids"]:
            alert_id = gen["alert_ids"][0]
            response = client.get(f"/v1/alerts/{alert_id}/audit")
            assert response.status_code == 200
            data = response.json()
            assert data["event_count"] >= 1
            assert len(data["events"]) >= 1

    @patch("app.api.alerts.load_transactions", return_value=MOCK_TRANSACTIONS)
    def test_audit_event_schema(self, _):
        gen = client.post("/v1/alerts/generate", json={"limit": 5}).json()
        if gen["alert_ids"]:
            alert_id = gen["alert_ids"][0]
            events   = client.get(f"/v1/alerts/{alert_id}/audit").json()["events"]
            if events:
                e = events[0]
                assert "event_type"  in e
                assert "description" in e
                assert "actor"       in e
                assert "timestamp"   in e

    def test_audit_trail_404_for_unknown_alert(self):
        response = client.get(
            "/v1/alerts/00000000-0000-0000-0000-000000000000/audit"
        )
        assert response.status_code == 404


# ── Data source endpoint ──────────────────────────────────────────────────────

class TestDataSource:
    def test_datasource_endpoint_returns_200(self):
        response = client.get("/v1/alerts/datasource")
        assert response.status_code == 200

    def test_datasource_has_required_fields(self):
        data = client.get("/v1/alerts/datasource").json()
        assert "active_source"      in data
        assert "ieee_cis_available" in data
        assert "synthetic_available" in data

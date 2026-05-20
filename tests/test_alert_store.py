"""
tests/test_alert_store.py
──────────────────────────
Unit tests for the in-memory alert store.

Run with:
    uv run pytest tests/test_alert_store.py -v
"""

import pytest

from app.services.alert_store import AlertStore
from app.shared.models import Alert, AlertStatus, AlertTrigger, AuditEvent


@pytest.fixture
def fresh_store() -> AlertStore:
    """A fresh store instance for each test."""
    s = AlertStore()
    return s


def make_alert(**kwargs) -> Alert:
    defaults = {
        "tx_id": "TX-001",
        "customer_id": "CUST001",
        "trigger": AlertTrigger.HIGH_VALUE,
    }
    defaults.update(kwargs)
    return Alert(**defaults)


class TestAlertStore:
    def test_save_and_get(self, fresh_store):
        alert = make_alert()
        fresh_store.save(alert)
        retrieved = fresh_store.get(str(alert.alert_id))
        assert retrieved is not None
        assert retrieved.alert_id == alert.alert_id

    def test_get_returns_none_for_missing(self, fresh_store):
        result = fresh_store.get("00000000-0000-0000-0000-000000000000")
        assert result is None

    def test_count_increments(self, fresh_store):
        assert fresh_store.count() == 0
        fresh_store.save(make_alert())
        assert fresh_store.count() == 1
        fresh_store.save(make_alert())
        assert fresh_store.count() == 2

    def test_list_all_newest_first(self, fresh_store):
        a1 = make_alert(tx_id="TX-001")
        a2 = make_alert(tx_id="TX-002")
        fresh_store.save(a1)
        fresh_store.save(a2)
        all_alerts = fresh_store.list_all()
        assert len(all_alerts) == 2

    def test_list_by_status(self, fresh_store):
        a1 = make_alert()
        a1.status = AlertStatus.PENDING
        a2 = make_alert()
        a2.status = AlertStatus.FRAUD_CONFIRMED
        fresh_store.save(a1)
        fresh_store.save(a2)
        pending = fresh_store.list_by_status(AlertStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].alert_id == a1.alert_id

    def test_clear_wipes_everything(self, fresh_store):
        fresh_store.save(make_alert())
        fresh_store.save(make_alert())
        assert fresh_store.count() == 2
        fresh_store.clear()
        assert fresh_store.count() == 0

    def test_audit_log(self, fresh_store):
        alert = make_alert()
        fresh_store.save(alert)
        event = AuditEvent(
            alert_id=str(alert.alert_id),
            event_type="TEST_EVENT",
            description="Test audit entry",
            actor="test",
        )
        fresh_store.log_event(event)
        trail = fresh_store.get_audit_trail(str(alert.alert_id))
        assert len(trail) == 1
        assert trail[0].event_type == "TEST_EVENT"

    def test_stats_structure(self, fresh_store):
        stats = fresh_store.stats()
        assert "total" in stats
        assert "by_status" in stats
        assert "by_risk_band" in stats
        assert stats["total"] == 0

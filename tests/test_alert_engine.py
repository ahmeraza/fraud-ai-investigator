"""
tests/test_alert_engine.py
───────────────────────────
Smoke tests for the alert engine.
Full rule logic is covered by test_alerts_api.py integration tests.
"""

from app.services.alert_engine import AlertEngine


class TestAlertEngine:
    def test_engine_initialises(self):
        """Alert engine loads without errors."""
        engine = AlertEngine()
        assert engine is not None

    def test_engine_has_evaluate_method(self):
        engine = AlertEngine()
        assert hasattr(engine, "evaluate")

    def test_engine_has_evaluate_batch_method(self):
        engine = AlertEngine()
        assert hasattr(engine, "evaluate_batch")

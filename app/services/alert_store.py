"""
app/services/alert_store.py
────────────────────────────
In-memory alert store — holds all alerts for the lifetime of the API process.

Why in-memory for now:
  - Zero dependencies (no database to set up)
  - Fast for demo and testing
  - Easy to replace with SQLite or PostgreSQL in a later week
  - Thread-safe via a simple lock

The store is a singleton — imported once and shared across all API requests.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.shared.models import Alert, AlertStatus, AuditEvent
from app.core.logging import get_logger

logger = get_logger(__name__)


class AlertStore:
    """
    Thread-safe in-memory store for alerts and audit events.

    Usage:
        from app.services.alert_store import store

        store.save(alert)
        alert = store.get(alert_id)
        all_alerts = store.list_all()
    """

    def __init__(self) -> None:
        self._alerts: dict[str, Alert] = {}
        self._audit_log: list[AuditEvent] = []
        self._lock = threading.Lock()

    # ── Alert CRUD ────────────────────────────────────────────────────────────

    def save(self, alert: Alert) -> Alert:
        """Insert or update an alert."""
        with self._lock:
            alert.updated_at = datetime.utcnow()
            self._alerts[str(alert.alert_id)] = alert
            logger.debug(f"Saved alert {alert.alert_id} | status={alert.status}")
            return alert

    def get(self, alert_id: str | UUID) -> Optional[Alert]:
        """Retrieve a single alert by ID. Returns None if not found."""
        with self._lock:
            return self._alerts.get(str(alert_id))

    def list_all(self) -> list[Alert]:
        """Return all alerts, newest first."""
        with self._lock:
            return sorted(
                self._alerts.values(),
                key=lambda a: a.created_at,
                reverse=True,
            )

    def list_by_status(self, status: AlertStatus) -> list[Alert]:
        """Return alerts filtered by status."""
        with self._lock:
            return [a for a in self._alerts.values() if a.status == status]

    def count(self) -> int:
        with self._lock:
            return len(self._alerts)

    def clear(self) -> None:
        """Wipe all alerts — used in tests only."""
        with self._lock:
            self._alerts.clear()
            self._audit_log.clear()

    # ── Audit log ─────────────────────────────────────────────────────────────

    def log_event(self, event: AuditEvent) -> None:
        """Append an immutable audit event."""
        with self._lock:
            self._audit_log.append(event)

    def get_audit_trail(self, alert_id: str) -> list[AuditEvent]:
        """Return all audit events for a specific alert, oldest first."""
        with self._lock:
            return [e for e in self._audit_log if e.alert_id == alert_id]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Summary stats for the dashboard."""
        with self._lock:
            alerts = list(self._alerts.values())
            return {
                "total": len(alerts),
                "by_status": {
                    status.value: sum(1 for a in alerts if a.status == status)
                    for status in AlertStatus
                },
                "by_risk_band": {
                    "LOW": sum(1 for a in alerts if a.risk_score is not None and a.risk_score < 30),
                    "MEDIUM": sum(1 for a in alerts if a.risk_score is not None and 30 <= a.risk_score < 70),
                    "HIGH": sum(1 for a in alerts if a.risk_score is not None and 70 <= a.risk_score < 90),
                    "CRITICAL": sum(1 for a in alerts if a.risk_score is not None and a.risk_score >= 90),
                    "UNSCORED": sum(1 for a in alerts if a.risk_score is None),
                },
            }


# ── Singleton instance ────────────────────────────────────────────────────────
# Import this everywhere: from app.services.alert_store import store
store = AlertStore()

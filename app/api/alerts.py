"""
app/api/alerts.py
──────────────────
FastAPI router for alert ingestion and retrieval.

Endpoints:
  POST /v1/alerts                — manually submit a transaction for review
  POST /v1/alerts/generate       — auto-generate alerts from synthetic dataset
  GET  /v1/alerts                — list all alerts (with optional filters)
  GET  /v1/alerts/{alert_id}     — get a single alert by ID
  GET  /v1/alerts/stats          — summary statistics
  GET  /v1/alerts/{id}/audit     — full audit trail for a case

This is the entry point of the 6-phase fraud investigation pipeline.
Everything downstream (triage, investigation, HITL) reads from the alert store.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.alert_engine import AlertEngine
from app.services.alert_store import store
from app.shared.models import (
    Alert,
    AlertCreateRequest,
    AlertResponse,
    AlertStatus,
    AlertTrigger,
    AuditEvent,
    Transaction,
)

logger = get_logger(__name__)
router = APIRouter()

# AlertEngine is initialised once when the router module loads
# This loads KYC + sanctions data from disk once at startup
_engine = AlertEngine()


# ── Request / Response schemas specific to this router ────────────────────────


class GenerateAlertsRequest(BaseModel):
    """Request body for POST /v1/alerts/generate."""
    limit: int = 10
    flagged_only: bool = False


class GenerateAlertsResponse(BaseModel):
    alerts_created: int
    alert_ids: list[str]
    message: str


class AlertListResponse(BaseModel):
    total: int
    alerts: list[AlertResponse]


# ── Helper ────────────────────────────────────────────────────────────────────


def _load_transactions() -> list[Transaction]:
    """Load synthetic transactions from the data file."""
    path = Path(__file__).parent.parent / "data" / "transactions.json"
    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail="transactions.json not found. Run: uv run python scripts/generate_data.py",
        )
    with open(path) as f:
        raw = json.load(f)

    transactions = []
    for item in raw:
        try:
            item["amount_aed"] = Decimal(str(item["amount_aed"]))
            transactions.append(Transaction(**item))
        except Exception as e:
            logger.warning(f"Skipping invalid transaction: {e}")
    return transactions


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/generate",
    response_model=GenerateAlertsResponse,
    summary="Auto-generate alerts from synthetic dataset",
    description=(
        "Runs the alert engine against the synthetic transaction dataset "
        "and creates alerts for every rule that fires. "
        "Use this to populate the system for demo and testing."
    ),
)
def generate_alerts(body: GenerateAlertsRequest) -> GenerateAlertsResponse:
    """
    Load synthetic transactions, run all rules, create alerts.
    This is the fastest way to see the system in action.
    """
    transactions = _load_transactions()

    if body.flagged_only:
        transactions = [t for t in transactions if t.is_flagged]

    transactions = transactions[: body.limit]

    logger.info(f"Running alert engine on {len(transactions)} transactions")
    alerts = _engine.evaluate_batch(transactions)

    return GenerateAlertsResponse(
        alerts_created=len(alerts),
        alert_ids=[str(a.alert_id) for a in alerts],
        message=(
            f"Evaluated {len(transactions)} transactions. "
            f"Created {len(alerts)} alerts across "
            f"{len(set(a.tx_id for a in alerts))} transactions."
        ),
    )


@router.post(
    "",
    response_model=AlertResponse,
    status_code=201,
    summary="Manually submit a transaction alert",
    description="Create a single alert by providing a transaction ID and customer ID.",
)
def create_alert(body: AlertCreateRequest) -> AlertResponse:
    """
    Manually raise an alert for a specific transaction.
    Useful for analyst-initiated reviews.
    """
    # Try to find the transaction in the dataset
    transactions = _load_transactions()
    tx = next((t for t in transactions if t.tx_id == body.tx_id), None)

    if tx is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction {body.tx_id} not found in dataset.",
        )

    from app.shared.models import Alert, AlertStatus
    alert = Alert(
        tx_id=body.tx_id,
        customer_id=body.customer_id,
        trigger=body.trigger,
        status=AlertStatus.PENDING,
    )
    store.save(alert)
    store.log_event(AuditEvent(
        alert_id=str(alert.alert_id),
        event_type="ALERT_CREATED_MANUAL",
        description=f"Alert manually created by analyst | trigger={body.trigger.value}",
        actor="analyst",
        metadata={"tx_id": body.tx_id, "trigger": body.trigger.value},
    ))

    logger.info(f"Manual alert created: {alert.alert_id}")
    return AlertResponse.from_alert(alert)


@router.get(
    "/stats",
    summary="Alert summary statistics",
)
def get_stats() -> dict:
    """Returns counts by status and risk band — used by the dashboard."""
    return store.stats()


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List all alerts",
)
def list_alerts(
    status: Optional[AlertStatus] = Query(default=None, description="Filter by status"),
    trigger: Optional[AlertTrigger] = Query(default=None, description="Filter by trigger"),
    limit: int = Query(default=50, le=200),
) -> AlertListResponse:
    """
    List all alerts, newest first.
    Optional filters: status, trigger type.
    """
    if status:
        alerts = store.list_by_status(status)
    else:
        alerts = store.list_all()

    if trigger:
        alerts = [a for a in alerts if a.trigger == trigger]

    alerts = alerts[:limit]

    return AlertListResponse(
        total=store.count(),
        alerts=[AlertResponse.from_alert(a) for a in alerts],
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get a single alert by ID",
)
def get_alert(alert_id: str) -> AlertResponse:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    return AlertResponse.from_alert(alert)


@router.get(
    "/{alert_id}/audit",
    summary="Get the full audit trail for an alert",
)
def get_audit_trail(alert_id: str) -> dict:
    """
    Returns the complete, immutable event timeline for a case.
    Every state change is logged here — required for regulatory compliance.
    """
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    events = store.get_audit_trail(alert_id)
    return {
        "alert_id": alert_id,
        "current_status": alert.status,
        "event_count": len(events),
        "events": [
            {
                "event_id": str(e.event_id),
                "event_type": e.event_type,
                "description": e.description,
                "actor": e.actor,
                "timestamp": e.timestamp.isoformat(),
                "metadata": e.metadata,
            }
            for e in events
        ],
    }

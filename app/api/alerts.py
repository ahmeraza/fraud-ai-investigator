"""
app/api/alerts.py
──────────────────
Alerts router — updated for IEEE-CIS Priority 2.

Change: generate endpoint now uses unified data_loader
so it automatically serves IEEE-CIS transactions when available.
All endpoints and schemas unchanged.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.alert_engine import AlertEngine
from app.services.alert_store import store
from app.services.data_loader import data_source_status, load_transactions
from app.shared.models import (
    Alert, AlertCreateRequest, AlertResponse,
    AlertStatus, AlertTrigger, AuditEvent,
)

logger = get_logger(__name__)
router = APIRouter()

_engine: Optional[AlertEngine] = None


def _get_engine() -> AlertEngine:
    global _engine
    if _engine is None:
        _engine = AlertEngine()
    return _engine


class GenerateAlertsRequest(BaseModel):
    limit       : int  = 10
    flagged_only: bool = False
    source      : str  = "auto"  # "auto" | "ieee" | "synthetic" | "combined"


class GenerateAlertsResponse(BaseModel):
    alerts_created: int
    alert_ids     : list[str]
    data_source   : str
    message       : str


class AlertListResponse(BaseModel):
    total : int
    alerts: list[AlertResponse]


@router.post(
    "/generate",
    response_model=GenerateAlertsResponse,
    summary="Auto-generate alerts from transaction dataset",
    description=(
        "Runs the alert engine against transactions. "
        "Uses IEEE-CIS real data if downloaded, otherwise synthetic. "
        "Set source='ieee', 'synthetic', or 'combined' to override."
    ),
)
def generate_alerts(body: GenerateAlertsRequest) -> GenerateAlertsResponse:
    transactions_dict = load_transactions(source=body.source)

    if not transactions_dict:
        raise HTTPException(
            status_code=500,
            detail=(
                "No transactions available. "
                "Run: uv run python scripts/generate_data.py  (synthetic) "
                "OR: uv run python scripts/load_ieee_data.py  (IEEE-CIS real data)"
            ),
        )

    transactions = list(transactions_dict.values())

    if body.flagged_only:
        transactions = [t for t in transactions if t.is_flagged]

    transactions = transactions[:body.limit]

    # Determine which source is actually being used
    if any(t.tx_id.startswith("IEEE-") for t in transactions):
        source_label = "ieee_cis"
    else:
        source_label = "synthetic"

    logger.info(
        f"Generating alerts | count={len(transactions)} | source={source_label}"
    )

    alerts = _get_engine().evaluate_batch(transactions)

    return GenerateAlertsResponse(
        alerts_created = len(alerts),
        alert_ids      = [str(a.alert_id) for a in alerts],
        data_source    = source_label,
        message        = (
            f"Evaluated {len(transactions)} transactions ({source_label}). "
            f"Created {len(alerts)} alerts."
        ),
    )


@router.get(
    "/datasource",
    summary="Transaction data source status",
    description="Shows whether IEEE-CIS or synthetic data is active.",
)
def get_datasource_status() -> dict:
    return data_source_status()


@router.post(
    "/stats",
    summary="Alert summary statistics",
)
def get_stats() -> dict:
    return store.stats()


@router.get(
    "/stats",
    summary="Alert summary statistics",
)
def get_stats_get() -> dict:
    return store.stats()


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List all alerts",
)
def list_alerts(
    status : Optional[AlertStatus]  = Query(default=None),
    trigger: Optional[AlertTrigger] = Query(default=None),
    limit  : int = Query(default=50, le=200),
) -> AlertListResponse:
    alerts = store.list_by_status(status) if status else store.list_all()
    if trigger:
        alerts = [a for a in alerts if a.trigger == trigger]
    return AlertListResponse(
        total  = store.count(),
        alerts = [AlertResponse.from_alert(a) for a in alerts[:limit]],
    )


@router.post(
    "",
    response_model=AlertResponse,
    status_code=201,
    summary="Manually submit a transaction alert",
)
def create_alert(body: AlertCreateRequest) -> AlertResponse:
    transactions = load_transactions()
    tx = transactions.get(body.tx_id)

    if not tx:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction {body.tx_id} not found.",
        )

    alert = Alert(
        tx_id       = body.tx_id,
        customer_id = body.customer_id,
        trigger     = body.trigger,
        status      = AlertStatus.PENDING,
    )
    store.save(alert)
    store.log_event(AuditEvent(
        alert_id    = str(alert.alert_id),
        event_type  = "ALERT_CREATED_MANUAL",
        description = f"Manual alert | trigger={body.trigger.value}",
        actor       = "analyst",
        metadata    = {"tx_id": body.tx_id, "trigger": body.trigger.value},
    ))
    return AlertResponse.from_alert(alert)


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get a single alert",
)
def get_alert(alert_id: str) -> AlertResponse:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    return AlertResponse.from_alert(alert)


@router.get(
    "/{alert_id}/audit",
    summary="Audit trail for an alert",
)
def get_audit_trail(alert_id: str) -> dict:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    events = store.get_audit_trail(alert_id)
    return {
        "alert_id"    : alert_id,
        "current_status": alert.status,
        "event_count" : len(events),
        "events"      : [
            {
                "event_type" : e.event_type,
                "description": e.description,
                "actor"      : e.actor,
                "timestamp"  : e.timestamp.isoformat(),
                "metadata"   : e.metadata,
            }
            for e in events
        ],
    }

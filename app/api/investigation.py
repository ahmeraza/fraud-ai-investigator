"""
app/api/investigation.py
─────────────────────────
FastAPI router — LangGraph investigation endpoints.

Route ordering note:
  /batch MUST be registered before /{alert_id}.
  FastAPI matches routes in registration order — without this ordering,
  POST /v1/investigate/batch resolves to alert_id="batch" and returns 404.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.logging import get_logger
from app.graph.investigation_graph import build_investigation_graph, run_investigation
from app.services.alert_store import store
from app.shared.models import AlertStatus

logger = get_logger(__name__)
router = APIRouter()

_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_investigation_graph()
    return _graph


class InvestigateRequest(BaseModel):
    wallet_address: Optional[str] = None
    force         : bool = False


class BatchInvestigateRequest(BaseModel):
    max_alerts    : int = 3
    wallet_address: Optional[str] = None


class InvestigationResult(BaseModel):
    alert_id              : str
    tx_id                 : str
    customer_id           : str
    trigger               : str
    final_risk_score      : Optional[int]
    final_risk_band       : Optional[str]
    recommendation        : Optional[str]
    investigation_summary : Optional[str]
    agents_completed      : list[str]
    signal_count          : int
    error_count           : int
    alert_status          : str


# ── /batch MUST come before /{alert_id} ──────────────────────────────────────

@router.post(
    "/batch",
    summary="Investigate multiple INVESTIGATING alerts",
    description=(
        "Runs full LangGraph investigation on up to max_alerts alerts. "
        "Default 3 to stay within Gemini free-tier rate limits."
    ),
)
def batch_investigate(body: BatchInvestigateRequest) -> dict:
    all_alerts = store.list_all()
    targets    = [
        a for a in all_alerts
        if a.status in (AlertStatus.INVESTIGATING, AlertStatus.PENDING)
    ][:body.max_alerts]

    if not targets:
        raise HTTPException(
            status_code=400,
            detail=(
                "No INVESTIGATING or PENDING alerts found. "
                "Run POST /v1/triage/batch first to move alerts to INVESTIGATING."
            ),
        )

    graph   = _get_graph()
    results = []

    for alert in targets:
        try:
            final_state = run_investigation(
                graph          = graph,
                alert_id       = str(alert.alert_id),
                tx_id          = alert.tx_id,
                customer_id    = alert.customer_id,
                trigger        = alert.trigger.value,
                wallet_address = body.wallet_address,
            )
            results.append({
                "alert_id"        : str(alert.alert_id),
                "status"          : "completed",
                "final_risk_score": final_state.get("final_risk_score"),
                "recommendation"  : final_state.get("recommendation"),
                "agents_completed": final_state.get("agents_completed", []),
            })
        except Exception as e:
            logger.error(f"Batch investigation failed | alert={alert.alert_id} | {e}")
            results.append({"alert_id": str(alert.alert_id), "status": "failed", "error": str(e)})

    succeeded = sum(1 for r in results if r["status"] == "completed")
    return {
        "investigated": len(results),
        "succeeded"   : succeeded,
        "failed"      : len(results) - succeeded,
        "results"     : results,
    }


@router.get("/stats", summary="Investigation pipeline statistics")
def investigation_stats() -> dict:
    all_alerts = store.list_all()
    stats = {
        "total_alerts"    : len(all_alerts),
        "pending"         : 0,
        "investigating"   : 0,
        "awaiting_human"  : 0,
        "auto_closed"     : 0,
        "fraud_confirmed" : 0,
        "false_positive"  : 0,
    }
    scores = [a.risk_score for a in all_alerts if a.risk_score is not None]
    for alert in all_alerts:
        key = alert.status.value.lower()
        if key in stats:
            stats[key] += 1
    stats["investigated_count"] = sum(1 for a in all_alerts if a.triage_narrative is not None)
    stats["average_risk_score"] = round(sum(scores) / len(scores), 1) if scores else None
    stats["graph_compiled"]     = _graph is not None
    return stats


# ── /{alert_id} routes come AFTER /batch and /stats ──────────────────────────

@router.post(
    "/{alert_id}",
    response_model=InvestigationResult,
    summary="Run full LangGraph investigation on one alert",
)
def investigate_alert(alert_id: str, body: InvestigateRequest) -> InvestigationResult:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    if alert.status == AlertStatus.AUTO_CLOSED and not body.force:
        raise HTTPException(
            status_code=400,
            detail=f"Alert {alert_id} is AUTO_CLOSED. Pass force=true to re-investigate.",
        )

    try:
        final_state = run_investigation(
            graph          = _get_graph(),
            alert_id       = alert_id,
            tx_id          = alert.tx_id,
            customer_id    = alert.customer_id,
            trigger        = alert.trigger.value,
            wallet_address = body.wallet_address,
        )
    except Exception as e:
        logger.error(f"Investigation failed | alert={alert_id} | error={e}")
        raise HTTPException(status_code=500, detail=f"Investigation failed: {e}")

    updated = store.get(alert_id)
    return InvestigationResult(
        alert_id              = alert_id,
        tx_id                 = alert.tx_id,
        customer_id           = alert.customer_id,
        trigger               = alert.trigger.value,
        final_risk_score      = final_state.get("final_risk_score"),
        final_risk_band       = final_state.get("final_risk_band"),
        recommendation        = final_state.get("recommendation"),
        investigation_summary = final_state.get("investigation_summary"),
        agents_completed      = final_state.get("agents_completed", []),
        signal_count          = len(final_state.get("risk_signals", [])),
        error_count           = len(final_state.get("errors", [])),
        alert_status          = updated.status.value if updated else "UNKNOWN",
    )


@router.get(
    "/{alert_id}/result",
    summary="Get investigation result for one alert",
)
def get_investigation_result(alert_id: str) -> dict:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    if not alert.triage_narrative:
        raise HTTPException(
            status_code=400,
            detail=f"Alert {alert_id} has not been investigated yet.",
        )
    return {
        "alert_id"             : alert_id,
        "status"               : alert.status.value,
        "risk_score"           : alert.risk_score,
        "risk_band"            : alert.risk_band.value if alert.risk_band else None,
        "investigation_summary": alert.triage_narrative,
        "trigger"              : alert.trigger.value,
        "customer_id"          : alert.customer_id,
        "tx_id"                : alert.tx_id,
        "updated_at"           : alert.updated_at.isoformat(),
    }

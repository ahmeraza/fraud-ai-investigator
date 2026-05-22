"""
app/api/hitl.py
────────────────
FastAPI router — Human-in-the-Loop review endpoints.

Endpoints:
  GET  /v1/hitl/queue                  — list alerts awaiting analyst review
  GET  /v1/hitl/{alert_id}/context     — get full context for one alert
  POST /v1/hitl/{alert_id}/decision    — submit analyst verdict
  GET  /v1/hitl/memory/stats           — fraud memory statistics
  GET  /v1/hitl/memory/cases           — list all recorded cases

Route ordering:
  /queue, /memory/stats, /memory/cases must be registered BEFORE /{alert_id}
  to prevent FastAPI treating them as alert IDs.

Phase 6 integration:
  The Streamlit dashboard will call these endpoints to build the
  analyst review panel. The Swagger UI provides the same interface
  for testing without the dashboard.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.services.fraud_memory import get_memory_stats, retrieve_similar_cases, _load_memory
from app.services.hitl_service import HITLService, VALID_VERDICTS
from app.shared.models import AlertStatus

logger  = get_logger(__name__)
router  = APIRouter()
_service = HITLService()


# ── Request schema ────────────────────────────────────────────────────────────

class HITLDecisionRequest(BaseModel):
    verdict     : str = Field(
        description=f"One of: {', '.join(sorted(VALID_VERDICTS))}"
    )
    analyst     : str = Field(
        min_length=2,
        description="Analyst identifier (email, username, or employee ID)",
    )
    notes       : str = Field(
        min_length=20,
        description="Mandatory decision rationale — minimum 20 characters",
    )
    risk_signals: list[str] = Field(
        default_factory=list,
        description="Signals considered in making this decision",
    )


# ── Endpoints — static routes first ──────────────────────────────────────────

@router.get(
    "/queue",
    summary="List alerts awaiting analyst review",
    description=(
        "Returns all alerts in AWAITING_HUMAN status, ordered by risk score "
        "descending (highest risk first). This is the analyst's work queue."
    ),
)
def get_review_queue() -> dict:
    from app.services.alert_store import store

    all_alerts = store.list_all()
    queue      = [
        a for a in all_alerts
        if a.status == AlertStatus.AWAITING_HUMAN
    ]

    # Sort by risk score descending — highest risk first
    queue.sort(key=lambda a: a.risk_score or 0, reverse=True)

    return {
        "queue_length": len(queue),
        "alerts"      : [
            {
                "alert_id"  : str(a.alert_id),
                "trigger"   : a.trigger.value,
                "customer_id": a.customer_id,
                "tx_id"     : a.tx_id,
                "risk_score": a.risk_score,
                "risk_band" : a.risk_band.value if a.risk_band else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in queue
        ],
    }


@router.get("/memory/stats", summary="Fraud memory statistics")
def memory_stats() -> dict:
    """Returns counts of confirmed fraud vs false positives in memory."""
    return get_memory_stats()


@router.get("/memory/cases", summary="List all recorded fraud memory cases")
def memory_cases() -> dict:
    """Returns all cases recorded in fraud memory."""
    entries = _load_memory()
    return {
        "total"  : len(entries),
        "cases"  : entries,
    }


# ── Dynamic routes — after static routes ─────────────────────────────────────

@router.get(
    "/{alert_id}/context",
    summary="Get full review context for one alert",
    description=(
        "Returns everything an analyst needs to make a verdict: "
        "investigation summary, similar past cases, regulatory guidance, "
        "and the full audit trail."
    ),
)
def get_review_context(alert_id: str) -> dict:
    try:
        return _service.get_review_context(alert_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/{alert_id}/decision",
    summary="Submit analyst HITL verdict",
    description=(
        "Submit an analyst verdict on an investigated alert. "
        "Verdicts: CONFIRMED_FRAUD | FALSE_POSITIVE | ESCALATED | NEEDS_MORE_INFO. "
        "CONFIRMED_FRAUD triggers an STR obligation under CBUAE AML/CFT guidelines."
    ),
)
def submit_decision(alert_id: str, body: HITLDecisionRequest) -> dict:
    try:
        result = _service.process_decision(
            alert_id     = alert_id,
            verdict      = body.verdict,
            analyst      = body.analyst,
            notes        = body.notes,
            risk_signals = body.risk_signals,
        )
        return result.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

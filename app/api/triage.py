"""
app/api/triage.py
──────────────────
FastAPI router — LLM triage endpoints (Phase 3).

Endpoints:
  POST /v1/triage/batch       → triage all PENDING alerts
  POST /v1/triage/{alert_id}  → triage one specific alert
  GET  /v1/triage/stats       → pipeline counts
  GET  /v1/triage/{alert_id}  → get stored triage result

Pipeline flow:
  POST /v1/alerts/generate  (Phase 2 — creates PENDING alerts)
  POST /v1/triage/batch     (Phase 3 — LLM scores each alert)
  POST /v1/investigate/...  (Phase 4 — agents deep-dive INVESTIGATING alerts)
"""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.services.alert_store import store
from app.services.triage_service import TriageResult, TriageService
from app.shared.models import AlertStatus

logger = get_logger(__name__)
router = APIRouter()

# Initialised once when module loads — pre-loads transaction + KYC data
_triage_service = TriageService()


# ── Schemas ───────────────────────────────────────────────────────────────────

class BatchTriageRequest(BaseModel):
    max_alerts: int = 5
    # Default 5 respects Gemini free-tier rate limits during development.
    # Increase to 20 for a full demo run.


class TriageResultResponse(BaseModel):
    alert_id          : str
    severity_score    : int
    severity_band     : str
    initial_suspicion : str
    risk_factors      : list[str]
    recommended_action: str
    regulatory_flags  : list[str]
    confidence        : str
    llm_provider      : str
    latency_ms        : float
    alert_status      : str

    @classmethod
    def from_result(
        cls,
        alert_id    : str,
        result      : TriageResult,
        alert_status: str,
    ) -> "TriageResultResponse":
        return cls(
            alert_id          = alert_id,
            severity_score    = result.severity_score,
            severity_band     = result.severity_band.value,
            initial_suspicion = result.initial_suspicion,
            risk_factors      = result.risk_factors,
            recommended_action= result.recommended_action,
            regulatory_flags  = result.regulatory_flags,
            confidence        = result.confidence,
            llm_provider      = result.llm_provider,
            latency_ms        = round(result.latency_ms, 1),
            alert_status      = alert_status,
        )


class BatchTriageResponse(BaseModel):
    processed   : int
    succeeded   : int
    failed      : int
    auto_closed : int
    investigating: int
    escalated   : int
    results     : list[TriageResultResponse]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/batch",
    response_model=BatchTriageResponse,
    summary="Triage all PENDING alerts",
    description=(
        "Runs LLM triage on all PENDING alerts up to max_alerts. "
        "Each alert is scored 0-100 and moved to INVESTIGATING, "
        "AUTO_CLOSED, or AWAITING_HUMAN. Default limit 5 respects free-tier rate limits."
    ),
)
def batch_triage(body: BatchTriageRequest) -> BatchTriageResponse:
    all_alerts = store.list_all()
    pending    = [a for a in all_alerts if a.status == AlertStatus.PENDING]

    if not pending:
        raise HTTPException(
            status_code=400,
            detail=(
                "No PENDING alerts found. "
                "Run POST /v1/alerts/generate first."
            ),
        )

    results_map = _triage_service.triage_batch(
        alerts     = all_alerts,
        max_alerts = body.max_alerts,
    )

    result_responses = []
    auto_closed = investigating = escalated = failed = 0

    for alert_id, result in results_map.items():
        if result is None:
            failed += 1
            continue
        alert = store.get(alert_id)
        if alert and alert.status == AlertStatus.AUTO_CLOSED:
            auto_closed += 1
        elif alert and alert.status == AlertStatus.AWAITING_HUMAN:
            escalated += 1
        else:
            investigating += 1
        result_responses.append(
            TriageResultResponse.from_result(
                alert_id     = alert_id,
                result       = result,
                alert_status = alert.status.value if alert else "UNKNOWN",
            )
        )

    # Highest risk first
    result_responses.sort(key=lambda r: r.severity_score, reverse=True)

    return BatchTriageResponse(
        processed    = len(results_map),
        succeeded    = len(results_map) - failed,
        failed       = failed,
        auto_closed  = auto_closed,
        investigating= investigating,
        escalated    = escalated,
        results      = result_responses,
    )


@router.post(
    "/{alert_id}",
    response_model=TriageResultResponse,
    summary="Triage a single alert",
)
def triage_single(alert_id: str) -> TriageResultResponse:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    if alert.status != AlertStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Alert is {alert.status.value}. Only PENDING alerts can be triaged.",
        )
    result = _triage_service.triage(alert)
    if result is None:
        raise HTTPException(status_code=500, detail="Triage failed — check server logs.")
    alert = store.get(alert_id)
    return TriageResultResponse.from_result(
        alert_id     = alert_id,
        result       = result,
        alert_status = alert.status.value if alert else "UNKNOWN",
    )


@router.get("/stats", summary="Triage pipeline statistics")
def triage_stats() -> dict:
    """Returns counts at each pipeline stage."""
    all_alerts  = store.list_all()
    stats       = {
        "total_alerts"   : len(all_alerts),
        "PENDING"        : 0,
        "TRIAGING"       : 0,
        "INVESTIGATING"  : 0,
        "AUTO_CLOSED"    : 0,
        "AWAITING_HUMAN" : 0,
        "FRAUD_CONFIRMED": 0,
        "FALSE_POSITIVE" : 0,
    }
    score_sum = 0
    scored    = 0
    for alert in all_alerts:
        key = alert.status.value
        if key in stats:
            stats[key] += 1
        if alert.risk_score is not None:
            score_sum += alert.risk_score
            scored    += 1
    stats["average_risk_score"] = round(score_sum / scored, 1) if scored else None
    stats["scored_alerts"]      = scored
    return stats


@router.get("/{alert_id}", summary="Get triage result for one alert")
def get_triage_result(alert_id: str) -> dict:
    alert = store.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    if alert.triage_narrative is None:
        raise HTTPException(
            status_code=400,
            detail=f"Alert {alert_id} has not been triaged (status: {alert.status.value}).",
        )
    return {
        "alert_id"        : alert_id,
        "status"          : alert.status.value,
        "risk_score"      : alert.risk_score,
        "risk_band"       : alert.risk_band.value if alert.risk_band else None,
        "triage_narrative": alert.triage_narrative,
        "trigger"         : alert.trigger.value,
        "customer_id"     : alert.customer_id,
        "tx_id"           : alert.tx_id,
        "updated_at"      : alert.updated_at.isoformat(),
    }

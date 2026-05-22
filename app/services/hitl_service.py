"""
app/services/hitl_service.py
──────────────────────────────
Human-in-the-Loop (HITL) service — analyst decision processing.

What this does:
  Receives an analyst's verdict on an investigated alert, validates it,
  updates the alert status, records the decision to fraud memory, and
  writes a comprehensive audit event. This is the final step in the
  investigation pipeline before an alert is closed.

HITL verdicts:
  CONFIRMED_FRAUD  → alert moves to FRAUD_CONFIRMED, STR filing required
  FALSE_POSITIVE   → alert moves to FALSE_POSITIVE, case closed
  ESCALATED        → alert moves to AWAITING_HUMAN (senior analyst queue)
  NEEDS_MORE_INFO  → alert stays INVESTIGATING, investigation re-runs

Regulatory significance:
  In a real UAE AML system, CONFIRMED_FRAUD triggers an automatic
  Suspicious Transaction Report (STR) to CBUAE within 2 working days.
  The audit trail produced here IS the evidence package that accompanies
  the STR filing. Every field is designed with that regulatory use case
  in mind — analyst ID, timestamp, decision rationale, all signals considered.

Phase 6 integration:
  The Streamlit dashboard will call POST /v1/hitl/{alert_id}/decision
  through a form interface. The analyst sees the investigation summary,
  past similar cases from fraud memory, and submits their verdict.
  This service processes that submission identically whether it comes
  from Streamlit or the Swagger UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger
from app.services.alert_store import store
from app.services.fraud_memory import record_outcome, retrieve_similar_cases
from app.shared.models import Alert, AlertStatus, AuditEvent

logger = get_logger(__name__)

# Valid verdicts an analyst can submit
VALID_VERDICTS = {
    "CONFIRMED_FRAUD",
    "FALSE_POSITIVE",
    "ESCALATED",
    "NEEDS_MORE_INFO",
}

# Alert status after each verdict
VERDICT_TO_STATUS = {
    "CONFIRMED_FRAUD": AlertStatus.FRAUD_CONFIRMED,
    "FALSE_POSITIVE" : AlertStatus.FALSE_POSITIVE,
    "ESCALATED"      : AlertStatus.AWAITING_HUMAN,
    "NEEDS_MORE_INFO": AlertStatus.INVESTIGATING,
}


class HITLDecisionResult:
    """Result of processing one analyst decision."""

    def __init__(
        self,
        alert_id     : str,
        verdict      : str,
        new_status   : str,
        memory_id    : str,
        analyst      : str,
        processed_at : str,
        str_required : bool,
    ) -> None:
        self.alert_id     = alert_id
        self.verdict      = verdict
        self.new_status   = new_status
        self.memory_id    = memory_id
        self.analyst      = analyst
        self.processed_at = processed_at
        self.str_required = str_required

    def to_dict(self) -> dict:
        return {
            "alert_id"    : self.alert_id,
            "verdict"     : self.verdict,
            "new_status"  : self.new_status,
            "memory_id"   : self.memory_id,
            "analyst"     : self.analyst,
            "processed_at": self.processed_at,
            "str_required": self.str_required,
            "str_deadline": (
                "Within 2 working days per CBUAE AML/CFT guidelines"
                if self.str_required else None
            ),
        }


class HITLService:
    """
    Processes analyst HITL decisions.

    Stateless — each call is independent. Can be instantiated
    per-request without any startup cost.
    """

    def process_decision(
        self,
        alert_id     : str,
        verdict      : str,
        analyst      : str,
        notes        : str,
        risk_signals : Optional[list[str]] = None,
    ) -> HITLDecisionResult:
        """
        Process one analyst verdict.

        Steps:
          1. Validate alert exists and is in a reviewable state
          2. Validate verdict is one of the allowed values
          3. Update alert status
          4. Record outcome to fraud memory
          5. Write detailed audit event
          6. Return result with STR obligation flag

        Args:
            alert_id    : UUID of the alert being decided
            verdict     : CONFIRMED_FRAUD | FALSE_POSITIVE | ESCALATED | NEEDS_MORE_INFO
            analyst     : analyst identifier (email, username, or ID)
            notes       : mandatory analyst notes explaining the decision
            risk_signals: signals the analyst considered (from investigation state)
        """
        # ── Validate ──────────────────────────────────────────────────────────
        if verdict not in VALID_VERDICTS:
            raise ValueError(
                f"Invalid verdict '{verdict}'. "
                f"Must be one of: {', '.join(sorted(VALID_VERDICTS))}"
            )

        alert = store.get(alert_id)
        if not alert:
            raise ValueError(f"Alert {alert_id} not found.")

        if alert.status not in (
            AlertStatus.AWAITING_HUMAN,
            AlertStatus.INVESTIGATING,
        ):
            raise ValueError(
                f"Alert {alert_id} is in {alert.status.value} status. "
                "Only AWAITING_HUMAN or INVESTIGATING alerts can receive HITL decisions."
            )

        if len(notes.strip()) < 20:
            raise ValueError(
                "Analyst notes must be at least 20 characters. "
                "Provide a meaningful explanation for the decision."
            )

        # ── Update alert status ───────────────────────────────────────────────
        new_status   = VERDICT_TO_STATUS[verdict]
        alert.status = new_status
        store.save(alert)

        processed_at = datetime.now(timezone.utc).isoformat()
        str_required = verdict == "CONFIRMED_FRAUD"

        # ── Record to fraud memory ────────────────────────────────────────────
        memory_entry = record_outcome(
            alert_id      = alert_id,
            tx_id         = alert.tx_id,
            customer_id   = alert.customer_id,
            trigger       = alert.trigger.value,
            country       = None,   # populated from investigation state if available
            merchant      = None,
            risk_score    = alert.risk_score,
            verdict       = verdict,
            analyst       = analyst,
            analyst_notes = notes,
            risk_signals  = risk_signals or [],
        )

        # ── Write comprehensive audit event ───────────────────────────────────
        audit_description = (
            f"HITL decision | verdict={verdict} | analyst={analyst} | "
            f"new_status={new_status.value}"
        )
        if str_required:
            audit_description += " | STR_REQUIRED: file within 2 working days"

        store.log_event(AuditEvent(
            alert_id    = alert_id,
            event_type  = "HITL_DECISION",
            description = audit_description,
            actor       = f"analyst:{analyst}",
            metadata    = {
                "verdict"          : verdict,
                "analyst"          : analyst,
                "analyst_notes"    : notes,
                "new_status"       : new_status.value,
                "risk_score"       : alert.risk_score,
                "str_required"     : str_required,
                "memory_id"        : memory_entry["memory_id"],
                "processed_at"     : processed_at,
                "risk_signals_count": len(risk_signals or []),
            },
        ))

        logger.info(
            f"HITL decision processed | alert={alert_id} | "
            f"verdict={verdict} | analyst={analyst} | "
            f"new_status={new_status.value} | "
            f"str_required={str_required}"
        )

        return HITLDecisionResult(
            alert_id     = alert_id,
            verdict      = verdict,
            new_status   = new_status.value,
            memory_id    = memory_entry["memory_id"],
            analyst      = analyst,
            processed_at = processed_at,
            str_required = str_required,
        )

    def get_review_context(self, alert_id: str) -> dict:
        """
        Build the full context an analyst needs to make a decision.

        Returns:
          - Alert details and investigation summary
          - Similar past cases from fraud memory
          - Regulatory guidance based on trigger type
          - Full audit trail

        This is what the Streamlit HITL review panel displays in Phase 6.
        """
        alert = store.get(alert_id)
        if not alert:
            raise ValueError(f"Alert {alert_id} not found.")

        # Retrieve similar past cases
        past_cases = retrieve_similar_cases(
            customer_id = alert.customer_id,
            trigger     = alert.trigger.value,
            max_results = 3,
        )

        # Regulatory guidance based on trigger
        regulatory_guidance = []
        if alert.trigger.value == "SANCTIONED_CORRIDOR":
            regulatory_guidance.append(
                "OFAC/FATF: Transactions to sanctioned corridors require "
                "enhanced due diligence before processing."
            )
        if alert.risk_score and alert.risk_score >= 70:
            regulatory_guidance.append(
                "CBUAE: High-risk alert — document decision rationale "
                "for potential STR filing."
            )
        regulatory_guidance.append(
            "CBUAE AML/CFT: STR must be filed within 2 working days "
            "of confirming suspicious activity."
        )

        # Full audit trail
        audit_events = store.get_audit_trail(alert_id)

        return {
            "alert": {
                "alert_id"             : alert_id,
                "status"               : alert.status.value,
                "trigger"              : alert.trigger.value,
                "customer_id"          : alert.customer_id,
                "tx_id"                : alert.tx_id,
                "risk_score"           : alert.risk_score,
                "risk_band"            : alert.risk_band.value if alert.risk_band else None,
                "investigation_summary": alert.triage_narrative,
                "created_at"           : alert.created_at.isoformat(),
                "updated_at"           : alert.updated_at.isoformat(),
            },
            "similar_past_cases"   : past_cases,
            "regulatory_guidance"  : regulatory_guidance,
            "valid_verdicts"       : sorted(VALID_VERDICTS),
            "audit_event_count"    : len(audit_events),
            "audit_trail"          : [
                {
                    "event_type" : e.event_type,
                    "description": e.description,
                    "actor"      : e.actor,
                    "timestamp"  : e.timestamp.isoformat(),
                }
                for e in audit_events
            ],
        }

"""
app/agents/synthesis_agent.py
──────────────────────────────
Synthesis Agent — final investigation node before HITL.

Responsibility:
  Collect all findings from transaction_agent, kyc_agent, sanctions_agent,
  and (optionally) crypto_agent, then call the LLM to produce:
  - A composite risk score (0-100) based on all signals
  - A comprehensive investigation narrative for the analyst
  - A final recommendation (CLEAR / INVESTIGATE / ESCALATE / BLOCK)

Why use an LLM here instead of rule-based scoring?
  The individual agents produce deterministic signals (binary: detected or not).
  The synthesis step requires judgment — weighing multiple signals together,
  considering their interaction, and producing an explanation a human can act on.
  A rule-based weighting (e.g. sanctions_hit * 40 + high_value * 20) produces
  a number but not a narrative. The LLM does both simultaneously.

Graph position: LAST node before END (or HITL interrupt in Phase 5).
  All agent outputs have been accumulated in state by the time this runs.
  transaction_agent + kyc_agent + sanctions_agent + crypto_agent
  → [this] synthesis_agent → END (or HITL in Phase 5)

Phase 5 compatibility:
  This agent's output populates state["investigation_summary"] and
  state["recommendation"]. Phase 5 adds a HITL interrupt after this node
  where an analyst reviews these fields and makes a decision.
  The synthesis_agent itself needs no changes in Phase 5.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState
from app.llm.client import get_llm_client
from app.services.alert_store import store
from app.shared.models import AlertStatus, AuditEvent

logger = get_logger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are a senior AML compliance officer at a UAE bank.
You have received findings from multiple specialist agents investigating a fraud alert.
Synthesise all findings into a final risk assessment.

Respond with ONLY a valid JSON object:
{
  "risk_score": <integer 0-100>,
  "risk_band": <"LOW"|"MEDIUM"|"HIGH"|"CRITICAL">,
  "investigation_summary": <string, 3-5 sentences covering all key findings>,
  "key_concerns": [<string>, ...],
  "recommendation": <"CLEAR"|"MONITOR"|"INVESTIGATE"|"ESCALATE"|"BLOCK">,
  "regulatory_obligations": [<string>, ...],
  "confidence": <"LOW"|"MEDIUM"|"HIGH">
}

Recommendation guide:
  CLEAR       → no material risk indicators, safe to process
  MONITOR     → low-level signals, flag for ongoing monitoring
  INVESTIGATE → multiple signals, requires analyst review before processing
  ESCALATE    → high confidence fraud indicators, senior analyst required
  BLOCK       → sanctions hit or critical evidence, block transaction immediately

UAE regulatory context:
  CBUAE requires STR filing within 2 working days of detecting suspicious activity.
  FATF requires enhanced due diligence for high-risk countries.
  VARA requires crypto mixer screening for digital asset transactions."""


def _build_synthesis_prompt(state: InvestigationState) -> str:
    """Build the synthesis prompt with all accumulated evidence."""
    lines = [
        "=== FRAUD INVESTIGATION — SYNTHESIS REQUEST ===",
        "",
        f"Alert ID    : {state['alert_id']}",
        f"Transaction : {state['tx_id']}",
        f"Customer    : {state['customer_id']}",
        f"Trigger     : {state['trigger']}",
        "",
        "--- TRANSACTION SUMMARY ---",
        state.get("transaction_summary") or "Not available",
        "",
        f"--- RISK SIGNALS ({len(state.get('risk_signals', []))}) ---",
    ]

    for i, signal in enumerate(state.get("risk_signals", []), 1):
        lines.append(f"  {i}. {signal}")

    lines += [
        "",
        f"--- REGULATORY FLAGS ({len(state.get('regulatory_flags', []))}) ---",
    ]
    for flag in state.get("regulatory_flags", []):
        lines.append(f"  ⚖ {flag}")

    crypto_signals = state.get("crypto_signals", [])
    if crypto_signals:
        lines += ["", f"--- CRYPTO SIGNALS ({len(crypto_signals)}) ---"]
        for cs in crypto_signals:
            lines.append(f"  ₿ {cs}")

    agents_done = state.get("agents_completed", [])
    errors      = state.get("errors", [])
    lines += [
        "",
        f"--- INVESTIGATION METADATA ---",
        f"Agents completed : {', '.join(agents_done)}",
        f"Errors           : {len(errors)}",
    ]
    if errors:
        for err in errors[:3]:
            lines.append(f"  ! {err}")

    lines += [
        "",
        "=== END OF INVESTIGATION DATA ===",
        "",
        "Produce your synthesis assessment as a JSON object.",
    ]
    return "\n".join(lines)


def synthesis_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Synthesise all agent findings into a final risk assessment.
    Calls the LLM — Gemini Flash primary, Groq fallback.
    Updates the alert in the store with the final score and narrative.
    """
    logger.info(
        f"[SynthesisAgent] Starting | alert={state['alert_id']} | "
        f"signals={len(state.get('risk_signals', []))} | "
        f"agents={state.get('agents_completed', [])}"
    )

    try:
        llm    = get_llm_client()
        prompt = _build_synthesis_prompt(state)
        response = llm.complete(prompt=prompt, system_prompt=SYNTHESIS_SYSTEM_PROMPT)
        raw      = response.parse_json()

        risk_score   = int(raw.get("risk_score", 50))
        risk_band    = raw.get("risk_band", "MEDIUM")
        summary      = raw.get("investigation_summary", "Investigation complete.")
        recommendation = raw.get("recommendation", "INVESTIGATE")
        key_concerns = raw.get("key_concerns", [])
        reg_obligations = raw.get("regulatory_obligations", [])

        # ── Update alert in store with investigation results ──────────────────
        alert_id = state["alert_id"]
        alert    = store.get(alert_id)

        if alert:
            alert.risk_score       = risk_score
            alert.triage_narrative = summary
            # Transition status based on recommendation
            if recommendation == "CLEAR":
                alert.status = AlertStatus.AUTO_CLOSED
            elif recommendation in ("ESCALATE", "BLOCK"):
                alert.status = AlertStatus.AWAITING_HUMAN
            else:
                alert.status = AlertStatus.INVESTIGATING
            store.save(alert)

            store.log_event(AuditEvent(
                alert_id    = alert_id,
                event_type  = "INVESTIGATION_COMPLETE",
                description = (
                    f"LangGraph investigation complete | "
                    f"score={risk_score} | band={risk_band} | "
                    f"recommendation={recommendation} | "
                    f"provider={response.provider}"
                ),
                actor    = "synthesis_agent",
                metadata = {
                    "risk_score"          : risk_score,
                    "risk_band"           : risk_band,
                    "recommendation"      : recommendation,
                    "key_concerns"        : key_concerns[:5],
                    "regulatory_obligations": reg_obligations[:3],
                    "agents_completed"    : state.get("agents_completed", []),
                    "total_signals"       : len(state.get("risk_signals", [])),
                    "llm_provider"        : response.provider,
                    "llm_latency_ms"      : round(response.latency_ms, 1),
                },
            ))

        logger.info(
            f"[SynthesisAgent] Complete | alert={alert_id} | "
            f"score={risk_score} | band={risk_band} | "
            f"recommendation={recommendation} | "
            f"provider={response.provider}"
        )

        return {
            "final_risk_score"     : risk_score,
            "final_risk_band"      : risk_band,
            "investigation_summary": summary,
            "recommendation"       : recommendation,
            "agents_completed"     : ["synthesis_agent"],
            "errors"               : [],
            "risk_signals"         : [],
            "regulatory_flags"     : [],
            "findings"             : [{
                "agent"              : "synthesis_agent",
                "status"             : "complete",
                "risk_score"         : risk_score,
                "risk_band"          : risk_band,
                "recommendation"     : recommendation,
                "key_concerns"       : key_concerns,
                "regulatory_obligations": reg_obligations,
                "llm_provider"       : response.provider,
                "llm_latency_ms"     : round(response.latency_ms, 1),
            }],
            "crypto_signals": [],
        }

    except Exception as e:
        logger.error(f"[SynthesisAgent] Error | {e}")
        # Fallback — use rule-based scoring if LLM fails
        signal_count = len(state.get("risk_signals", []))
        fallback_score = min(signal_count * 20, 80)
        return {
            "final_risk_score"     : fallback_score,
            "final_risk_band"      : "HIGH" if fallback_score >= 70 else "MEDIUM",
            "investigation_summary": (
                f"LLM synthesis failed ({e}). "
                f"Rule-based fallback: {signal_count} signals detected. "
                f"Manual review required."
            ),
            "recommendation"       : "INVESTIGATE",
            "agents_completed"     : ["synthesis_agent"],
            "errors"               : [f"SynthesisAgent LLM error: {e}"],
            "risk_signals"         : [],
            "regulatory_flags"     : [],
            "findings"             : [{
                "agent"     : "synthesis_agent",
                "status"    : "fallback",
                "error"     : str(e),
                "risk_score": fallback_score,
            }],
            "crypto_signals": [],
        }

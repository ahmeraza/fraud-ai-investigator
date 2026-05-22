"""
app/agents/synthesis_agent.py
──────────────────────────────
Synthesis Agent — Phase 5 update: includes fraud memory context.

Change from Phase 4:
  Before calling the LLM, retrieves similar past cases from fraud memory
  and includes them in the prompt. This gives the LLM historical context:
  "The same customer was previously confirmed as fraud" or
  "Similar transactions to this corridor were previously false positives."

  This is the only change. The LLM call, Pydantic validation, state
  update, and audit logging are all identical to Phase 4.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState
from app.llm.client import get_llm_client
from app.services.alert_store import store
from app.services.fraud_memory import retrieve_similar_cases
from app.shared.models import AlertStatus, AuditEvent

logger = get_logger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are a senior AML compliance officer at a UAE bank.
Synthesise findings from multiple specialist agents into a final risk assessment.

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
  CLEAR       → no material risk, safe to process
  MONITOR     → low-level signals, flag for ongoing monitoring
  INVESTIGATE → multiple signals, requires analyst review
  ESCALATE    → high confidence fraud, senior analyst required
  BLOCK       → sanctions hit or critical evidence, block immediately

UAE context: CBUAE requires STR within 2 working days of confirmed suspicious activity."""


def _build_synthesis_prompt(state: InvestigationState, past_cases: list[dict]) -> str:
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
    for i, s in enumerate(state.get("risk_signals", []), 1):
        lines.append(f"  {i}. {s}")

    lines += ["", f"--- REGULATORY FLAGS ({len(state.get('regulatory_flags', []))}) ---"]
    for f in state.get("regulatory_flags", []):
        lines.append(f"  ⚖ {f}")

    crypto_signals = state.get("crypto_signals", [])
    if crypto_signals:
        lines += ["", f"--- CRYPTO SIGNALS ({len(crypto_signals)}) ---"]
        for cs in crypto_signals:
            lines.append(f"  ₿ {cs}")

    # ── Phase 5 addition: fraud memory context ────────────────────────────────
    if past_cases:
        lines += ["", f"--- FRAUD MEMORY: {len(past_cases)} SIMILAR PAST CASE(S) ---"]
        for case in past_cases:
            lines.append(
                f"  [{case['verdict']}] Customer {case['customer_id']} | "
                f"trigger={case.get('trigger')} | score={case.get('risk_score')} | "
                f"notes: {case.get('analyst_notes', '')[:100]}"
            )
        lines.append(
            "  NOTE: Weight past confirmed fraud cases heavily. "
            "Past false positives suggest lower score appropriate."
        )

    agents_done = state.get("agents_completed", [])
    lines += [
        "",
        f"--- METADATA ---",
        f"Agents: {', '.join(agents_done)} | Errors: {len(state.get('errors', []))}",
        "",
        "=== END OF INVESTIGATION DATA ===",
        "",
        "Produce your synthesis assessment as a JSON object.",
    ]
    return "\n".join(lines)


def synthesis_agent(state: InvestigationState) -> dict[str, Any]:
    """Synthesise all agent findings with fraud memory context."""
    logger.info(
        f"[SynthesisAgent] Starting | alert={state['alert_id']} | "
        f"signals={len(state.get('risk_signals', []))}"
    )

    # ── Retrieve fraud memory (Phase 5 addition) ──────────────────────────────
    past_cases = retrieve_similar_cases(
        customer_id = state["customer_id"],
        trigger     = state["trigger"],
        max_results = 3,
    )
    if past_cases:
        logger.info(
            f"[SynthesisAgent] Fraud memory: {len(past_cases)} similar case(s) found"
        )

    try:
        llm    = get_llm_client()
        prompt = _build_synthesis_prompt(state, past_cases)
        resp   = llm.complete(prompt=prompt, system_prompt=SYNTHESIS_SYSTEM_PROMPT)
        raw    = resp.parse_json()

        risk_score    = int(raw.get("risk_score", 50))
        risk_band     = raw.get("risk_band", "MEDIUM")
        summary       = raw.get("investigation_summary", "Investigation complete.")
        recommendation= raw.get("recommendation", "INVESTIGATE")
        key_concerns  = raw.get("key_concerns", [])
        reg_obs       = raw.get("regulatory_obligations", [])

        # Update alert in store
        alert_id = state["alert_id"]
        alert    = store.get(alert_id)
        if alert:
            alert.risk_score       = risk_score
            alert.triage_narrative = summary
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
                    f"score={risk_score} | band={risk_band} | "
                    f"recommendation={recommendation} | "
                    f"provider={resp.provider} | "
                    f"memory_cases_used={len(past_cases)}"
                ),
                actor    = "synthesis_agent",
                metadata = {
                    "risk_score"          : risk_score,
                    "risk_band"           : risk_band,
                    "recommendation"      : recommendation,
                    "key_concerns"        : key_concerns[:5],
                    "regulatory_obligations": reg_obs[:3],
                    "agents_completed"    : state.get("agents_completed", []),
                    "total_signals"       : len(state.get("risk_signals", [])),
                    "llm_provider"        : resp.provider,
                    "llm_latency_ms"      : round(resp.latency_ms, 1),
                    "memory_cases_used"   : len(past_cases),
                },
            ))

        logger.info(
            f"[SynthesisAgent] Complete | alert={alert_id} | "
            f"score={risk_score} | band={risk_band} | rec={recommendation}"
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
                "regulatory_obligations": reg_obs,
                "llm_provider"       : resp.provider,
                "memory_cases_used"  : len(past_cases),
            }],
            "crypto_signals": [],
        }

    except Exception as e:
        logger.error(f"[SynthesisAgent] Error | {e}")
        signal_count   = len(state.get("risk_signals", []))
        fallback_score = min(signal_count * 20, 80)
        return {
            "final_risk_score"     : fallback_score,
            "final_risk_band"      : "HIGH" if fallback_score >= 70 else "MEDIUM",
            "investigation_summary": (
                f"LLM synthesis failed ({e}). "
                f"Rule-based fallback: {signal_count} signals. Manual review required."
            ),
            "recommendation"  : "INVESTIGATE",
            "agents_completed": ["synthesis_agent"],
            "errors"          : [f"SynthesisAgent error: {e}"],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "findings"        : [{"agent": "synthesis_agent", "status": "fallback", "error": str(e)}],
            "crypto_signals"  : [],
        }

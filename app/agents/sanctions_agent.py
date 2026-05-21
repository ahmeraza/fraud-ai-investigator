"""
app/agents/sanctions_agent.py
──────────────────────────────
Sanctions Agent — OFAC SDN screening during investigation.

Responsibility:
  Screen the transaction's merchant/counterparty name against the OFAC SDN
  list using the same SanctionsScreener built in the OFAC branch.
  Also screens the customer's name if available in the KYC profile.

Why re-screen during investigation?
  The alert engine ran a quick screen during rule evaluation.
  The investigation agent runs a more thorough screen:
  - Lower score threshold (catches PROBABLE matches, not just STRONG)
  - Screens customer name in addition to merchant
  - Returns full match evidence for the audit trail

Graph position: Runs in PARALLEL with kyc_agent after transaction_agent.
  Both are independent — sanctions screening doesn't need KYC data.

Technology: Uses the SanctionsScreener from app/services/sanctions_screener.py
  (built in the OFAC priority branch). This is the production OFAC SDN
  screener with Arabic name transliteration support.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState
from app.services.data_loader import load_kyc_profiles, load_transactions
from app.services.sanctions_screener import SanctionsScreener

logger = get_logger(__name__)

# Use a lower threshold during investigation (catches PROBABLE matches)
# The alert engine used 75 (STRONG). Investigation uses 50 (PROBABLE).
INVESTIGATION_THRESHOLD = 50


def sanctions_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Screen merchant and customer names against OFAC SDN list.

    Uses SanctionsScreener singleton — the index is built once and reused.
    Runs in parallel with kyc_agent — independent data source.
    """
    logger.info(
        f"[SanctionsAgent] Starting | alert={state['alert_id']} | "
        f"merchant={state.get('merchant', 'unknown')}"
    )

    try:
        screener         = SanctionsScreener()
        risk_signals    : list[str] = []
        regulatory_flags: list[str] = []
        screens_run     : list[dict] = []

        # ── Screen 1: Merchant name ───────────────────────────────────────────
        merchant = state.get("merchant") or ""
        country  = state.get("country") or ""

        if merchant and merchant != "unknown":
            result = screener.screen(
                name      = merchant,
                country   = country,
                threshold = INVESTIGATION_THRESHOLD,
            )
            screens_run.append({
                "screen_type" : "merchant",
                "query"       : merchant,
                "is_hit"      : result.is_hit,
                "best_score"  : result.best_score,
                "matches"     : [m.to_dict() for m in result.matches[:3]],
            })

            if result.is_hit and result.top_match:
                match = result.top_match
                risk_signals.append(
                    f"Merchant '{merchant}' matches OFAC SDN entity "
                    f"'{match.primary_name}' — score {result.best_score}/100 "
                    f"({match.severity})"
                )
                regulatory_flags.append(
                    f"OFAC: merchant matches sanctioned entity | "
                    f"programs: {', '.join(match.programs)} | "
                    f"action: {match.recommended_action}"
                )

        # ── Screen 2: Customer name (from KYC profile) ────────────────────────
        profiles = load_kyc_profiles()
        profile  = profiles.get(state["customer_id"])

        if profile and hasattr(profile, "name") and profile.name:
            customer_name = profile.name
            result2 = screener.screen(
                name      = customer_name,
                threshold = INVESTIGATION_THRESHOLD,
            )
            screens_run.append({
                "screen_type" : "customer_name",
                "query"       : customer_name,
                "is_hit"      : result2.is_hit,
                "best_score"  : result2.best_score,
                "matches"     : [m.to_dict() for m in result2.matches[:3]],
            })

            if result2.is_hit and result2.top_match:
                match2 = result2.top_match
                risk_signals.append(
                    f"Customer name '{customer_name}' matches OFAC SDN entity "
                    f"'{match2.primary_name}' — score {result2.best_score}/100 "
                    f"({match2.severity})"
                )
                regulatory_flags.append(
                    f"OFAC: customer name matches sanctioned individual | "
                    f"programs: {', '.join(match2.programs)} | "
                    f"IMMEDIATE ESCALATION REQUIRED"
                )

        hits_found = sum(1 for s in screens_run if s["is_hit"])

        finding = {
            "agent"           : "sanctions_agent",
            "status"          : "complete",
            "screens_run"     : len(screens_run),
            "hits_found"      : hits_found,
            "threshold_used"  : INVESTIGATION_THRESHOLD,
            "entity_count"    : screener.entity_count,
            "name_variant_count": screener.name_variant_count,
            "screens"         : screens_run,
        }

        logger.info(
            f"[SanctionsAgent] Complete | "
            f"screens={len(screens_run)} | hits={hits_found} | "
            f"entities_checked={screener.entity_count:,}"
        )

        return {
            "agents_completed": ["sanctions_agent"],
            "findings"        : [finding],
            "risk_signals"    : risk_signals,
            "regulatory_flags": regulatory_flags,
            "errors"          : [],
            "crypto_signals"  : [],
        }

    except Exception as e:
        logger.error(f"[SanctionsAgent] Error | {e}")
        return {
            "agents_completed": ["sanctions_agent"],
            "errors"          : [f"SanctionsAgent error: {e}"],
            "findings"        : [{"agent": "sanctions_agent", "status": "error", "error": str(e)}],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "crypto_signals"  : [],
        }

"""
app/agents/kyc_agent.py
────────────────────────
KYC Agent — identity and account risk assessment.

Responsibility:
  Load the customer's KYC profile and assess identity-based risk signals:
  - Device mismatch (account takeover indicator)
  - New account making large transactions (money mule pattern)
  - Customer risk tier set during onboarding
  - Nationality risk weighting

Graph position: Runs in PARALLEL with sanctions_agent after transaction_agent.
  LangGraph fans out to kyc_agent and sanctions_agent simultaneously,
  then waits for both before synthesis_agent runs.
  This halves the wall-clock investigation time vs sequential execution.

Phase 5 note:
  The KYC profile data here comes from the synthetic data store.
  In Phase 5 the HITL flow may trigger a fresh KYC check with an
  external identity verification provider (Jumio, Onfido) for
  HIGH/CRITICAL alerts. The agent interface stays the same.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState
from app.services.data_loader import load_kyc_profiles

logger = get_logger(__name__)

# Nationalities with elevated risk — UAE AML guidelines
HIGH_RISK_NATIONALITIES = {
    "IR", "KP", "SY", "MM", "YE", "SD",
}


def kyc_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Assess customer identity risk from KYC profile.

    Runs in parallel with sanctions_agent — both are independent
    data sources that don't need each other's output.
    """
    logger.info(
        f"[KYCAgent] Starting | alert={state['alert_id']} | "
        f"customer={state['customer_id']}"
    )

    try:
        profiles   = load_kyc_profiles()
        profile    = profiles.get(state["customer_id"])
        risk_signals    : list[str] = []
        regulatory_flags: list[str] = []

        if not profile:
            logger.warning(
                f"[KYCAgent] No KYC profile | customer={state['customer_id']}"
            )
            return {
                "agents_completed": ["kyc_agent"],
                "findings"        : [{
                    "agent"      : "kyc_agent",
                    "status"     : "no_profile",
                    "customer_id": state["customer_id"],
                    "note"       : "No KYC profile found — treat as unknown customer",
                }],
                "risk_signals"   : [
                    f"Customer {state['customer_id']} has no KYC profile — "
                    "identity unverified"
                ],
                "regulatory_flags": [
                    "CBUAE: unverified customer identity — KYC refresh required before processing"
                ],
                "errors"      : [],
                "crypto_signals": [],
            }

        # ── Assess risk signals from profile ──────────────────────────────────

        # Signal: device mismatch — strong account takeover indicator
        if profile.has_device_mismatch:
            risk_signals.append(
                f"Device fingerprint mismatch detected — current device differs "
                f"from KYC-verified device. Possible account takeover."
            )
            regulatory_flags.append(
                "CBUAE: device mismatch — account integrity check required"
            )

        # Signal: new account (< 30 days) making significant transaction
        if profile.is_new_account:
            risk_signals.append(
                f"Account is {profile.account_age_days} days old (< 30 days). "
                "New accounts with large transactions match money mule pattern."
            )
            regulatory_flags.append(
                "FATF: new account high-risk pattern — enhanced due diligence required"
            )

        # Signal: high-risk nationality
        if profile.nationality in HIGH_RISK_NATIONALITIES:
            risk_signals.append(
                f"Customer nationality {profile.nationality} is in "
                "FATF high-risk jurisdiction list"
            )

        # Signal: HIGH risk tier (set during onboarding)
        risk_tier = profile.risk_tier.value if hasattr(profile.risk_tier, 'value') else str(profile.risk_tier)
        if risk_tier == "HIGH":
            risk_signals.append(
                "Customer has HIGH risk tier set during KYC onboarding — "
                "may indicate PEP status, adverse media, or prior SAR"
            )
            regulatory_flags.append(
                "CBUAE: HIGH risk tier customer — enhanced ongoing monitoring required"
            )

        finding = {
            "agent"           : "kyc_agent",
            "status"          : "complete",
            "customer_id"     : state["customer_id"],
            "nationality"     : profile.nationality,
            "account_age_days": profile.account_age_days,
            "is_new_account"  : profile.is_new_account,
            "has_device_mismatch": profile.has_device_mismatch,
            "risk_tier"       : risk_tier,
            "risk_signal_count": len(risk_signals),
        }

        logger.info(
            f"[KYCAgent] Complete | customer={state['customer_id']} | "
            f"signals={len(risk_signals)} | tier={risk_tier}"
        )

        return {
            "agents_completed": ["kyc_agent"],
            "findings"        : [finding],
            "risk_signals"    : risk_signals,
            "regulatory_flags": regulatory_flags,
            "errors"          : [],
            "crypto_signals"  : [],
        }

    except Exception as e:
        logger.error(f"[KYCAgent] Error | {e}")
        return {
            "agents_completed": ["kyc_agent"],
            "errors"          : [f"KYCAgent error: {e}"],
            "findings"        : [{"agent": "kyc_agent", "status": "error", "error": str(e)}],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "crypto_signals"  : [],
        }

"""
app/agents/crypto_agent.py
───────────────────────────
Crypto Agent — on-chain transaction analysis.

Responsibility:
  If the investigation state contains a wallet address, screen it
  against the Etherscan API using the CryptoAlertEngine built in the
  crypto monitoring branch. Reports mixer detection results as risk signals.

When does this agent run?
  Only when state["wallet_address"] is populated.
  The graph uses a conditional edge after transaction_agent to decide:
    - wallet_address present → route to crypto_agent (parallel with kyc/sanctions)
    - wallet_address absent  → skip crypto_agent
  This keeps the graph efficient — we don't call Etherscan for
  every alert, only for transactions linked to crypto wallets.

Graph position:
  Conditional — only runs if wallet_address is present in state.
  When it runs: parallel with kyc_agent and sanctions_agent.
  When skipped: kyc_agent and sanctions_agent run, then synthesis directly.

Technology:
  Calls CryptoAlertEngine.screen_address() directly (not via HTTP).
  The same logic as POST /v1/crypto/screen but without the API overhead.
  This is the correct pattern for agent-to-agent calls — bypass HTTP,
  call the Python function directly.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState

logger = get_logger(__name__)


def crypto_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Screen wallet address for mixer usage and on-chain risk patterns.

    Returns immediately if no wallet_address in state (safe no-op).
    Requires ETHERSCAN_API_KEY to be configured in .env.
    """
    wallet = state.get("wallet_address")

    if not wallet:
        # No wallet to screen — skip gracefully
        return {
            "agents_completed": ["crypto_agent"],
            "errors"          : [],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "findings"        : [{
                "agent"  : "crypto_agent",
                "status" : "skipped",
                "reason" : "no wallet_address in investigation state",
            }],
            "crypto_signals"  : [],
        }

    logger.info(
        f"[CryptoAgent] Starting | alert={state['alert_id']} | "
        f"wallet={wallet[:10]}..."
    )

    try:
        from app.core.config import get_settings
        from app.crypto.crypto_alert_engine import (
            CryptoAlertEngine, CryptoScreeningRequest
        )
        settings = get_settings()

        if not settings.has_etherscan_key:
            logger.warning("[CryptoAgent] No Etherscan key — skipping on-chain analysis")
            return {
                "agents_completed": ["crypto_agent"],
                "errors"          : [],
                "risk_signals"    : ["Crypto screening skipped — ETHERSCAN_API_KEY not configured"],
                "regulatory_flags": [],
                "findings"        : [{
                    "agent"  : "crypto_agent",
                    "status" : "skipped",
                    "reason" : "ETHERSCAN_API_KEY not configured",
                    "wallet" : wallet,
                }],
                "crypto_signals": [],
            }

        engine  = CryptoAlertEngine(
            etherscan_api_key = settings.etherscan_api_key,
            score_threshold   = settings.crypto_mixer_score_threshold,
        )
        request = CryptoScreeningRequest(
            address     = wallet,
            customer_id = state["customer_id"],
            tx_id       = state["tx_id"],
            limit       = 50,  # limit for investigation (not exhaustive)
        )
        response = engine.screen_address(request)

        risk_signals    : list[str] = []
        regulatory_flags: list[str] = []
        crypto_signals  : list[str] = []

        if response.error:
            logger.warning(f"[CryptoAgent] Screening error: {response.error}")
            return {
                "agents_completed": ["crypto_agent"],
                "errors"          : [f"CryptoAgent: {response.error}"],
                "risk_signals"    : [],
                "regulatory_flags": [],
                "findings"        : [{
                    "agent"  : "crypto_agent",
                    "status" : "error",
                    "error"  : response.error,
                    "wallet" : wallet,
                }],
                "crypto_signals": [],
            }

        detection = response.detection_result
        if detection and detection.is_flagged:
            risk_signals.append(
                f"Wallet {wallet[:10]}... has on-chain mixer interaction detected. "
                f"Risk score: {detection.risk_score}/100 ({detection.severity}). "
                f"Direct mixer hits: {len(detection.direct_hits)}."
            )
            regulatory_flags.append(
                f"VARA/CBUAE: crypto mixer interaction detected | "
                f"score={detection.risk_score} | action={detection.recommended_action}"
            )
            for signal in detection.signals[:3]:
                crypto_signals.append(f"{signal.signal_type}: {signal.description[:100]}")

        finding = {
            "agent"           : "crypto_agent",
            "status"          : "complete",
            "wallet"          : wallet,
            "risk_score"      : detection.risk_score if detection else 0,
            "is_flagged"      : detection.is_flagged if detection else False,
            "severity"        : detection.severity if detection else "LOW",
            "direct_hits"     : detection.direct_hits[:3] if detection else [],
            "signal_count"    : len(detection.signals) if detection else 0,
            "tx_count_analysed": detection.transaction_count if detection else 0,
        }

        logger.info(
            f"[CryptoAgent] Complete | wallet={wallet[:10]}... | "
            f"score={detection.risk_score if detection else 0} | "
            f"flagged={detection.is_flagged if detection else False}"
        )

        return {
            "agents_completed": ["crypto_agent"],
            "crypto_risk_score": detection.risk_score if detection else 0,
            "findings"         : [finding],
            "risk_signals"     : risk_signals,
            "regulatory_flags" : regulatory_flags,
            "errors"           : [],
            "crypto_signals"   : crypto_signals,
        }

    except Exception as e:
        logger.error(f"[CryptoAgent] Error | {e}")
        return {
            "agents_completed": ["crypto_agent"],
            "errors"          : [f"CryptoAgent error: {e}"],
            "findings"        : [{"agent": "crypto_agent", "status": "error", "error": str(e)}],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "crypto_signals"  : [],
        }

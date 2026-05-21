"""
app/agents/transaction_agent.py
────────────────────────────────
Transaction Agent — first node in the investigation graph.

Responsibility:
  Load the transaction from the data store, extract key signals,
  and populate the state with transaction context for downstream agents.

Why this is a separate agent (not just loading data):
  In Phase 4 the transaction agent also calls the LLM to produce a
  concise transaction summary that downstream agents use as context.
  Separating data loading from LLM inference keeps each agent focused
  and makes the graph easier to test and debug.

What it produces:
  - transaction_summary: human-readable description for downstream agents
  - amount_aed, country, merchant: raw fields for rule evaluation
  - risk_signals: initial signals from transaction data alone
  - regulatory_flags: CBUAE/FATF flags that apply at transaction level
  - findings: structured evidence dict for audit trail

Graph position: START → [this] → kyc_agent (parallel with sanctions_agent)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.graph.state import InvestigationState
from app.services.data_loader import load_transactions

logger = get_logger(__name__)

# UAE CBUAE reporting threshold — same constant used in alert engine
HIGH_VALUE_THRESHOLD_AED = 40_000.0

# FATF 2024 high-risk jurisdictions — same list as config.py
HIGH_RISK_COUNTRIES = {
    "KP", "IR", "MM", "SY", "YE", "SD", "PK",
    "PH", "HT", "LA", "NG", "TZ", "CM", "CD",
    "AO", "CU", "VE", "BY", "LY", "RU",
}


def transaction_agent(state: InvestigationState) -> dict[str, Any]:
    """
    Load transaction data and extract risk signals.

    This is a pure function — takes state, returns a partial update dict.
    LangGraph merges the returned dict into the shared state automatically.
    No side effects other than logging.
    """
    logger.info(
        f"[TransactionAgent] Starting | alert={state['alert_id']} | "
        f"tx={state['tx_id']}"
    )

    try:
        # Load transaction from unified data loader (IEEE-CIS or synthetic)
        transactions = load_transactions()
        tx = transactions.get(state["tx_id"])

        if not tx:
            logger.warning(
                f"[TransactionAgent] Transaction not found | tx={state['tx_id']}"
            )
            return {
                "agents_completed"  : ["transaction_agent"],
                "errors"            : [f"Transaction {state['tx_id']} not found in data store"],
                "transaction_summary": f"Transaction {state['tx_id']} — data unavailable",
                "findings"          : [{
                    "agent"  : "transaction_agent",
                    "status" : "data_missing",
                    "tx_id"  : state["tx_id"],
                }],
                "risk_signals"     : [],
                "regulatory_flags" : [],
                "crypto_signals"   : [],
            }

        amount     = float(tx.amount_aed)
        country    = tx.country
        merchant   = tx.merchant
        timestamp  = tx.timestamp.strftime("%Y-%m-%d %H:%M UTC")
        hour       = tx.timestamp.hour

        # ── Extract risk signals ──────────────────────────────────────────────
        risk_signals    : list[str] = []
        regulatory_flags: list[str] = []

        # Signal 1: high-value transaction
        if amount > HIGH_VALUE_THRESHOLD_AED:
            risk_signals.append(
                f"Transaction amount AED {amount:,.0f} exceeds CBUAE "
                f"reporting threshold of AED {HIGH_VALUE_THRESHOLD_AED:,.0f}"
            )
            regulatory_flags.append(
                f"CBUAE: cash transaction above AED {HIGH_VALUE_THRESHOLD_AED:,.0f} "
                "requires reporting under AML/CFT regulations"
            )

        # Signal 2: FATF high-risk country
        if country in HIGH_RISK_COUNTRIES:
            risk_signals.append(
                f"Transaction routed through {country} — "
                "FATF high-risk jurisdiction (enhanced due diligence required)"
            )
            regulatory_flags.append(
                f"FATF: {country} on grey/black list — enhanced due diligence mandatory"
            )

        # Signal 3: overnight transaction (2-5 AM UTC)
        if 2 <= hour <= 5:
            risk_signals.append(
                f"Transaction initiated at {hour:02d}:00 UTC — "
                "overnight hours associated with elevated fraud risk"
            )

        # Signal 4: critically large amount
        if amount > 200_000:
            risk_signals.append(
                f"Critical value: AED {amount:,.0f} — senior analyst review required"
            )
            regulatory_flags.append(
                "CBUAE: transaction above AED 200,000 — consider SAR filing"
            )

        # ── Build human-readable summary ──────────────────────────────────────
        summary = (
            f"Transaction {tx.tx_id}: AED {amount:,.2f} "
            f"at {merchant} ({country}) on {timestamp}. "
            f"Trigger: {state['trigger']}. "
            f"Risk signals detected: {len(risk_signals)}."
        )

        finding = {
            "agent"           : "transaction_agent",
            "status"          : "complete",
            "tx_id"           : tx.tx_id,
            "amount_aed"      : amount,
            "country"         : country,
            "merchant"        : merchant,
            "timestamp"       : timestamp,
            "hour_utc"        : hour,
            "risk_signal_count": len(risk_signals),
            "is_high_value"   : amount > HIGH_VALUE_THRESHOLD_AED,
            "is_high_risk_country": country in HIGH_RISK_COUNTRIES,
        }

        logger.info(
            f"[TransactionAgent] Complete | tx={state['tx_id']} | "
            f"amount=AED {amount:,.0f} | country={country} | "
            f"signals={len(risk_signals)}"
        )

        return {
            "transaction_summary": summary,
            "amount_aed"         : amount,
            "country"            : country,
            "merchant"           : merchant,
            "risk_signals"       : risk_signals,
            "regulatory_flags"   : regulatory_flags,
            "findings"           : [finding],
            "agents_completed"   : ["transaction_agent"],
            "errors"             : [],
            "crypto_signals"     : [],
        }

    except Exception as e:
        logger.error(f"[TransactionAgent] Error | {e}")
        return {
            "agents_completed": ["transaction_agent"],
            "errors"          : [f"TransactionAgent error: {e}"],
            "risk_signals"    : [],
            "regulatory_flags": [],
            "findings"        : [{"agent": "transaction_agent", "status": "error", "error": str(e)}],
            "crypto_signals"  : [],
        }

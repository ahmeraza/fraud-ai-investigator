"""
app/graph/state.py
───────────────────
Shared state schema for the LangGraph investigation graph.

This is the single most important design decision in a LangGraph system.
The State is a TypedDict that all agents read from and write to.
Every node receives the full state and returns a partial update dict.
LangGraph merges updates automatically between nodes.

Design principles:
  1. State is small and typed — only fields every agent needs
  2. Lists use operator.add reducer — agents append, never overwrite
  3. Optional fields default to None — agents only populate what they know
  4. Immutable evidence trail — findings accumulate across all agents

Pipeline position:
  This state flows through the complete investigation graph:
    START
      → transaction_agent   (payment data analysis)
      → kyc_agent           (identity risk assessment)
      → sanctions_agent     (OFAC + FATF screening)
      → crypto_agent        (on-chain analysis, if wallet present)
      → synthesis_agent     (combines all findings → final score)
      → [HITL interrupt]    (Phase 5 — analyst review gate)
      → END

Phase 5 compatibility:
  The `hitl_decision` and `hitl_notes` fields are defined here now
  so Phase 5 can simply populate them without touching the state schema.
  This is forward-compatibility by design.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict


class InvestigationState(TypedDict):
    """
    Shared state for the fraud investigation graph.

    Fields annotated with Annotated[list, operator.add] use the
    add reducer — LangGraph appends each agent's output to the
    existing list rather than replacing it. This gives a complete
    evidence trail from all agents.

    Fields without a reducer use last-write-wins — later agents
    can update scalar values like risk_score and recommendation.
    """

    # ── Input (populated before graph starts) ────────────────────────────────
    alert_id   : str            # UUID of the alert being investigated
    tx_id      : str            # transaction ID
    customer_id: str            # customer identifier
    trigger    : str            # alert trigger type (e.g. HIGH_VALUE)

    # ── Transaction context (populated by transaction_agent) ─────────────────
    transaction_summary: Optional[str]   # human-readable transaction description
    amount_aed         : Optional[float] # transaction amount in AED
    country            : Optional[str]   # destination country
    merchant           : Optional[str]   # merchant name

    # ── Agent findings (accumulated with add reducer) ─────────────────────────
    # Each agent appends its findings — never overwrites previous agents' work
    findings: Annotated[list[dict[str, Any]], operator.add]

    # ── Risk signals (accumulated with add reducer) ───────────────────────────
    risk_signals: Annotated[list[str], operator.add]

    # ── Regulatory flags (accumulated with add reducer) ───────────────────────
    regulatory_flags: Annotated[list[str], operator.add]

    # ── Final synthesis (populated by synthesis_agent) ────────────────────────
    final_risk_score   : Optional[int]   # 0-100 composite score
    final_risk_band    : Optional[str]   # LOW / MEDIUM / HIGH / CRITICAL
    investigation_summary: Optional[str] # full narrative for analyst
    recommendation     : Optional[str]   # recommended action

    # ── Crypto analysis (populated by crypto_agent if wallet present) ─────────
    wallet_address     : Optional[str]   # Ethereum wallet (if known)
    crypto_risk_score  : Optional[int]   # on-chain mixer detection score
    crypto_signals     : Annotated[list[str], operator.add]

    # ── HITL fields (Phase 5) — defined here for forward compatibility ─────────
    hitl_decision : Optional[str]   # CONFIRMED_FRAUD / FALSE_POSITIVE / ESCALATE
    hitl_analyst  : Optional[str]   # analyst who made the decision
    hitl_notes    : Optional[str]   # analyst notes

    # ── Graph metadata ────────────────────────────────────────────────────────
    agents_completed: Annotated[list[str], operator.add]  # tracks which agents ran
    errors          : Annotated[list[str], operator.add]  # non-fatal errors from agents

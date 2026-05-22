"""
app/graph/investigation_graph.py
──────────────────────────────────
LangGraph investigation graph — Phase 5 update.

Changes from Phase 4:
  1. synthesis_agent now retrieves fraud memory before calling LLM
     — similar past cases are included in the synthesis prompt
  2. Graph structure unchanged — same nodes, same edges, same checkpointer
  3. run_investigation() signature unchanged — no callers need updating

The memory integration is entirely inside synthesis_agent's prompt building.
The graph wiring doesn't change at all.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.crypto_agent import crypto_agent
from app.agents.kyc_agent import kyc_agent
from app.agents.sanctions_agent import sanctions_agent
from app.agents.synthesis_agent import synthesis_agent
from app.agents.transaction_agent import transaction_agent
from app.core.logging import get_logger
from app.graph.state import InvestigationState

logger = get_logger(__name__)


def _route_after_transaction(
    state: InvestigationState,
) -> list[Literal["kyc_agent", "sanctions_agent", "crypto_agent"]]:
    next_nodes: list[str] = ["kyc_agent", "sanctions_agent"]
    if state.get("wallet_address"):
        next_nodes.append("crypto_agent")
        logger.info(f"[Graph] routing to crypto_agent | wallet={state['wallet_address'][:10]}...")
    return next_nodes


def build_investigation_graph() -> Any:
    """Build and compile the investigation graph with MemorySaver."""
    builder = StateGraph(InvestigationState)

    builder.add_node("transaction_agent", transaction_agent)
    builder.add_node("kyc_agent",         kyc_agent)
    builder.add_node("sanctions_agent",   sanctions_agent)
    builder.add_node("crypto_agent",      crypto_agent)
    builder.add_node("synthesis_agent",   synthesis_agent)

    builder.add_edge(START, "transaction_agent")

    builder.add_conditional_edges(
        "transaction_agent",
        _route_after_transaction,
        {
            "kyc_agent"      : "kyc_agent",
            "sanctions_agent": "sanctions_agent",
            "crypto_agent"   : "crypto_agent",
        },
    )

    builder.add_edge("kyc_agent",       "synthesis_agent")
    builder.add_edge("sanctions_agent", "synthesis_agent")
    builder.add_edge("crypto_agent",    "synthesis_agent")
    builder.add_edge("synthesis_agent", END)

    graph = builder.compile(checkpointer=MemorySaver())
    logger.info("Investigation graph compiled | nodes=5 | checkpointer=MemorySaver")
    return graph


def run_investigation(
    graph         : Any,
    alert_id      : str,
    tx_id         : str,
    customer_id   : str,
    trigger       : str,
    wallet_address: Optional[str] = None,
) -> InvestigationState:
    """Run a complete investigation for one alert."""
    initial_state: InvestigationState = {
        "alert_id"             : alert_id,
        "tx_id"                : tx_id,
        "customer_id"          : customer_id,
        "trigger"              : trigger,
        "wallet_address"       : wallet_address,
        "transaction_summary"  : None,
        "amount_aed"           : None,
        "country"              : None,
        "merchant"             : None,
        "findings"             : [],
        "risk_signals"         : [],
        "regulatory_flags"     : [],
        "crypto_signals"       : [],
        "final_risk_score"     : None,
        "final_risk_band"      : None,
        "investigation_summary": None,
        "recommendation"       : None,
        "crypto_risk_score"    : None,
        "hitl_decision"        : None,
        "hitl_analyst"         : None,
        "hitl_notes"           : None,
        "agents_completed"     : [],
        "errors"               : [],
    }

    config      = {"configurable": {"thread_id": alert_id}}
    final_state = graph.invoke(initial_state, config=config)

    logger.info(
        f"[Graph] Investigation complete | alert={alert_id} | "
        f"score={final_state.get('final_risk_score')} | "
        f"recommendation={final_state.get('recommendation')}"
    )
    return final_state

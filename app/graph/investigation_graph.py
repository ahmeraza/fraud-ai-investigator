"""
app/graph/investigation_graph.py
──────────────────────────────────
LangGraph investigation graph — wires all agents into a stateful workflow.

Architecture:
  START
    │
    ▼
  transaction_agent           ← loads tx data, extracts signals
    │
    ├──────────────────────────────────┐
    ▼                                  ▼
  kyc_agent (parallel)    sanctions_agent (parallel)
    │                                  │
    └──────────────┬───────────────────┘
                   │
              [conditional]
                   │
         wallet?───┘───no wallet?
         ▼                    ▼
    crypto_agent          (skip)
         │                    │
         └──────────┬─────────┘
                    ▼
             synthesis_agent    ← LLM final assessment
                    │
                   END
                   (Phase 5: HITL interrupt before END)

Key design decisions:

1. Parallel execution (kyc + sanctions run simultaneously)
   LangGraph's add_edge supports fan-out: one source, multiple targets.
   Both agents start at the same time, synthesis waits for both to finish.
   This halves wall-clock time vs sequential execution.

2. Conditional crypto agent
   We use add_conditional_edges to skip crypto_agent when no wallet is present.
   This keeps the happy path fast — most alerts don't have wallet addresses.

3. MemorySaver checkpointer
   Every node execution is checkpointed. This means:
   - You can inspect state at any point in the graph
   - Phase 5 HITL can interrupt the graph and resume after analyst decision
   - Crash recovery: the graph can resume from the last checkpoint

4. Thread ID = alert_id
   Each alert gets its own thread_id for the checkpointer.
   This isolates state between concurrent investigations.

Usage:
    graph  = build_investigation_graph()
    result = run_investigation(graph, alert_id, tx_id, customer_id, trigger)
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
    """
    Conditional routing after transaction_agent.

    Returns a list of next nodes to execute — LangGraph runs them in parallel.
    Always includes kyc_agent and sanctions_agent.
    Adds crypto_agent only if a wallet address is present in state.

    This is a fan-out: one node → multiple parallel nodes.
    """
    next_nodes: list[str] = ["kyc_agent", "sanctions_agent"]

    if state.get("wallet_address"):
        next_nodes.append("crypto_agent")
        logger.info(
            f"[Graph] Routing to crypto_agent | "
            f"wallet={state['wallet_address'][:10]}..."
        )
    else:
        logger.info("[Graph] No wallet address — skipping crypto_agent")

    return next_nodes


def _route_to_synthesis(
    state: InvestigationState,
) -> Literal["synthesis_agent"]:
    """
    Routes to synthesis_agent after all parallel agents complete.
    Simple pass-through — always goes to synthesis.
    In Phase 5 this will be replaced with an interrupt for HITL.
    """
    return "synthesis_agent"


def build_investigation_graph() -> Any:
    """
    Build and compile the investigation graph.

    Returns a compiled CompiledGraph ready for invocation.
    Call this once at startup and reuse the result — compilation
    is expensive, invocation is cheap.
    """
    builder = StateGraph(InvestigationState)

    # ── Register nodes ────────────────────────────────────────────────────────
    builder.add_node("transaction_agent", transaction_agent)
    builder.add_node("kyc_agent",         kyc_agent)
    builder.add_node("sanctions_agent",   sanctions_agent)
    builder.add_node("crypto_agent",      crypto_agent)
    builder.add_node("synthesis_agent",   synthesis_agent)

    # ── Entry point ───────────────────────────────────────────────────────────
    builder.add_edge(START, "transaction_agent")

    # ── Fan-out: transaction → [kyc, sanctions, (crypto)] in parallel ─────────
    builder.add_conditional_edges(
        "transaction_agent",
        _route_after_transaction,
        {
            "kyc_agent"      : "kyc_agent",
            "sanctions_agent": "sanctions_agent",
            "crypto_agent"   : "crypto_agent",
        },
    )

    # ── Fan-in: all parallel agents → synthesis ───────────────────────────────
    # LangGraph waits for ALL incoming edges before running synthesis_agent
    builder.add_edge("kyc_agent",       "synthesis_agent")
    builder.add_edge("sanctions_agent", "synthesis_agent")
    builder.add_edge("crypto_agent",    "synthesis_agent")

    # ── Terminal edge ─────────────────────────────────────────────────────────
    # Phase 5: replace with interrupt_before=["synthesis_agent"] for HITL
    builder.add_edge("synthesis_agent", END)

    # ── Compile with MemorySaver checkpointer ─────────────────────────────────
    # MemorySaver = in-memory, resets on restart.
    # Phase 5: replace with SqliteSaver or PostgresSaver for persistence.
    checkpointer = MemorySaver()
    graph        = builder.compile(checkpointer=checkpointer)

    logger.info(
        "Investigation graph compiled | "
        "nodes=5 | parallel=[kyc, sanctions, (crypto)] | "
        "checkpointer=MemorySaver"
    )
    return graph


def run_investigation(
    graph       : Any,
    alert_id    : str,
    tx_id       : str,
    customer_id : str,
    trigger     : str,
    wallet_address: Optional[str] = None,
) -> InvestigationState:
    """
    Run a complete investigation for one alert.

    Args:
        graph        : compiled LangGraph graph (from build_investigation_graph)
        alert_id     : UUID of the alert in the store
        tx_id        : transaction ID to investigate
        customer_id  : customer identifier
        trigger      : alert trigger type (e.g. "HIGH_VALUE")
        wallet_address: optional Ethereum wallet for on-chain screening

    Returns the final InvestigationState after all agents have run.
    """
    # Initial state — all agents start from this
    initial_state: InvestigationState = {
        "alert_id"            : alert_id,
        "tx_id"               : tx_id,
        "customer_id"         : customer_id,
        "trigger"             : trigger,
        "wallet_address"      : wallet_address,
        "transaction_summary" : None,
        "amount_aed"          : None,
        "country"             : None,
        "merchant"            : None,
        "findings"            : [],
        "risk_signals"        : [],
        "regulatory_flags"    : [],
        "crypto_signals"      : [],
        "final_risk_score"    : None,
        "final_risk_band"     : None,
        "investigation_summary": None,
        "recommendation"      : None,
        "crypto_risk_score"   : None,
        "hitl_decision"       : None,
        "hitl_analyst"        : None,
        "hitl_notes"          : None,
        "agents_completed"    : [],
        "errors"              : [],
    }

    # Thread ID = alert_id ensures each investigation has isolated state
    config = {"configurable": {"thread_id": alert_id}}

    logger.info(
        f"[Graph] Investigation started | alert={alert_id} | "
        f"tx={tx_id} | trigger={trigger} | "
        f"wallet={'yes' if wallet_address else 'no'}"
    )

    final_state = graph.invoke(initial_state, config=config)

    logger.info(
        f"[Graph] Investigation complete | alert={alert_id} | "
        f"score={final_state.get('final_risk_score')} | "
        f"recommendation={final_state.get('recommendation')} | "
        f"agents={final_state.get('agents_completed')}"
    )

    return final_state

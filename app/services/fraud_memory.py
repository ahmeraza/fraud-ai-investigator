"""
app/services/fraud_memory.py
──────────────────────────────
Fraud memory store — persists confirmed case outcomes for future investigations.

What is fraud memory?
  When an analyst confirms a case as CONFIRMED_FRAUD or FALSE_POSITIVE,
  that decision is stored as a "memory". Future investigations of similar
  transactions (same customer, same country corridor, same merchant pattern)
  retrieve relevant memories and include them as context for the synthesis
  agent, improving accuracy over time.

  This is the simplest form of institutional learning — the system gets
  smarter with every analyst decision without any model retraining.

Design:
  Storage: JSON file on disk (app/data/fraud_memory.json)
  Format : list of FraudMemoryEntry dicts
  Retrieval: similarity matching on customer_id, country, trigger type
  Phase 6: this feeds directly into the Streamlit dashboard's
           "Similar past cases" panel shown to analysts during review.
  Phase 7: on Hugging Face Spaces the memory file persists across restarts
           via Hugging Face's persistent storage volume.

Why not a database?
  For a portfolio project, a JSON file is sufficient and keeps the
  dependency count low. The interface is identical to what you'd use
  with SQLite or PostgreSQL — swap the storage backend without changing
  any caller code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

MEMORY_PATH = Path(__file__).parent.parent / "data" / "fraud_memory.json"


def _load_memory() -> list[dict]:
    """Load all memory entries from disk. Returns empty list if file missing."""
    if not MEMORY_PATH.exists():
        return []
    try:
        with open(MEMORY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load fraud memory: {e}")
        return []


def _save_memory(entries: list[dict]) -> None:
    """Persist memory entries to disk."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False, default=str)


def record_outcome(
    alert_id      : str,
    tx_id         : str,
    customer_id   : str,
    trigger       : str,
    country       : Optional[str],
    merchant      : Optional[str],
    risk_score    : Optional[int],
    verdict       : str,          # CONFIRMED_FRAUD | FALSE_POSITIVE | ESCALATED
    analyst       : str,
    analyst_notes : str,
    risk_signals  : list[str],
) -> dict:
    """
    Record the outcome of a HITL decision to fraud memory.

    Called by the HITL service after an analyst submits their verdict.
    The entry is immediately written to disk so it's available for
    the next investigation even if the server restarts.

    Returns the entry that was saved (for audit logging).
    """
    entry = {
        "memory_id"    : f"MEM-{alert_id[:8]}",
        "alert_id"     : alert_id,
        "tx_id"        : tx_id,
        "customer_id"  : customer_id,
        "trigger"      : trigger,
        "country"      : country,
        "merchant"     : merchant,
        "risk_score"   : risk_score,
        "verdict"      : verdict,
        "analyst"      : analyst,
        "analyst_notes": analyst_notes,
        "risk_signals" : risk_signals[:5],  # store top 5 signals only
        "recorded_at"  : datetime.now(timezone.utc).isoformat(),
    }

    entries = _load_memory()
    entries.append(entry)
    _save_memory(entries)

    logger.info(
        f"Fraud memory recorded | memory_id={entry['memory_id']} | "
        f"verdict={verdict} | customer={customer_id} | analyst={analyst}"
    )
    return entry


def retrieve_similar_cases(
    customer_id: str,
    country    : Optional[str] = None,
    trigger    : Optional[str] = None,
    max_results: int = 3,
) -> list[dict]:
    """
    Retrieve past cases similar to the current investigation.

    Similarity is determined by:
      1. Same customer_id (highest relevance — same person)
      2. Same country corridor (FATF risk pattern)
      3. Same trigger type (same rule fired)

    Results are ranked by relevance score and recency.
    Used by synthesis_agent to include historical context.

    Returns a list of simplified memory entries for LLM consumption.
    """
    entries = _load_memory()
    if not entries:
        return []

    scored: list[tuple[int, dict]] = []

    for entry in entries:
        score = 0

        # Same customer — strongest signal
        if entry.get("customer_id") == customer_id:
            score += 10

        # Same country corridor
        if country and entry.get("country") == country:
            score += 5

        # Same trigger type
        if trigger and entry.get("trigger") == trigger:
            score += 3

        if score > 0:
            scored.append((score, entry))

    # Sort by score descending, then recency descending
    scored.sort(key=lambda x: (x[0], x[1].get("recorded_at", "")), reverse=True)

    results = []
    for _, entry in scored[:max_results]:
        results.append({
            "memory_id"   : entry["memory_id"],
            "verdict"     : entry["verdict"],
            "customer_id" : entry["customer_id"],
            "country"     : entry.get("country"),
            "trigger"     : entry.get("trigger"),
            "risk_score"  : entry.get("risk_score"),
            "analyst_notes": entry.get("analyst_notes", "")[:200],
            "recorded_at" : entry.get("recorded_at", ""),
        })

    if results:
        logger.info(
            f"Fraud memory retrieved | customer={customer_id} | "
            f"matches={len(results)} | top_verdict={results[0]['verdict']}"
        )

    return results


def get_memory_stats() -> dict:
    """Return summary statistics about the fraud memory store."""
    entries = _load_memory()
    if not entries:
        return {
            "total_cases"     : 0,
            "confirmed_fraud" : 0,
            "false_positives" : 0,
            "escalated"       : 0,
            "unique_customers": 0,
            "memory_path"     : str(MEMORY_PATH),
        }

    verdicts   = [e.get("verdict", "") for e in entries]
    customers  = {e.get("customer_id") for e in entries}

    return {
        "total_cases"     : len(entries),
        "confirmed_fraud" : verdicts.count("CONFIRMED_FRAUD"),
        "false_positives" : verdicts.count("FALSE_POSITIVE"),
        "escalated"       : verdicts.count("ESCALATED"),
        "unique_customers": len(customers),
        "memory_path"     : str(MEMORY_PATH),
        "last_recorded"   : entries[-1].get("recorded_at") if entries else None,
    }


def clear_memory() -> None:
    """Clear all fraud memory entries. Used in tests only."""
    _save_memory([])
    logger.info("Fraud memory cleared")

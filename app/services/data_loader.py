"""
app/services/data_loader.py
────────────────────────────
Unified transaction data loader — serves both synthetic and IEEE-CIS data.

The alert engine and triage service previously loaded only the synthetic
Faker-generated transactions.json. This module replaces those direct file
reads with a unified loader that:

  1. Tries IEEE-CIS data first (real, credible data)
  2. Falls back to synthetic data if IEEE-CIS not downloaded yet
  3. Can blend both sources together

This means the system works on Day 1 without any Kaggle download,
but improves automatically the moment real data is available.

Usage:
    from app.services.data_loader import load_transactions, load_kyc_profiles

    transactions = load_transactions()  # always returns something
    profiles     = load_kyc_profiles()  # unchanged — KYC stays synthetic
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.shared.models import KYCProfile, Transaction

logger = get_logger(__name__)

DATA_DIR      = Path(__file__).parent.parent / "data"
SYNTHETIC_PATH = DATA_DIR / "transactions.json"
IEEE_PATH      = DATA_DIR / "ieee_cis" / "ieee_transactions.json"
KYC_PATH       = DATA_DIR / "kyc_profiles.json"


def _parse_transaction(item: dict) -> Optional[Transaction]:
    """Parse one raw dict into a Transaction model. Returns None on failure."""
    try:
        item = {**item, "amount_aed": Decimal(str(item["amount_aed"]))}
        return Transaction(**{
            k: v for k, v in item.items()
            if k in Transaction.model_fields
        })
    except Exception as e:
        logger.warning(f"Skipping invalid transaction: {e}")
        return None


def load_transactions(
    source: str = "auto",
    limit : Optional[int] = None,
) -> dict[str, Transaction]:
    """
    Load transactions keyed by tx_id.

    Args:
        source: "auto"     → IEEE-CIS if available, else synthetic (default)
                "ieee"     → IEEE-CIS only (raises if not downloaded)
                "synthetic"→ synthetic Faker data only
                "combined" → both sources merged

    Returns:
        dict mapping tx_id → Transaction
    """
    result: dict[str, Transaction] = {}

    if source in ("auto", "ieee", "combined"):
        if IEEE_PATH.exists():
            result.update(_load_from_file(IEEE_PATH, limit=limit))
            logger.info(
                f"Loaded IEEE-CIS transactions | count={len(result)} | "
                f"source={IEEE_PATH.name}"
            )
        elif source == "ieee":
            raise FileNotFoundError(
                f"IEEE-CIS data not found at {IEEE_PATH}. "
                "Run: uv run python scripts/load_ieee_data.py"
            )
        else:
            logger.info("IEEE-CIS data not available — will use synthetic")

    if source in ("auto", "synthetic", "combined") and not result:
        # Use synthetic if IEEE not available (auto) or explicitly requested
        if SYNTHETIC_PATH.exists():
            result.update(_load_from_file(SYNTHETIC_PATH, limit=limit))
            logger.info(
                f"Loaded synthetic transactions | count={len(result)} | "
                f"source={SYNTHETIC_PATH.name}"
            )
        else:
            logger.warning(
                "No transaction data found. "
                "Run: uv run python scripts/generate_data.py"
            )

    if source == "combined" and SYNTHETIC_PATH.exists():
        # Merge — synthetic adds to IEEE-CIS without overwriting
        synthetic = _load_from_file(SYNTHETIC_PATH, limit=limit)
        before = len(result)
        for tx_id, tx in synthetic.items():
            if tx_id not in result:
                result[tx_id] = tx
        logger.info(
            f"Combined mode | ieee={before} | added_synthetic={len(result)-before} "
            f"| total={len(result)}"
        )

    return result


def _load_from_file(
    path : Path,
    limit: Optional[int] = None,
) -> dict[str, Transaction]:
    """Load and parse transactions from a JSON file."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    if limit:
        raw = raw[:limit]

    result = {}
    for item in raw:
        tx = _parse_transaction(item)
        if tx:
            result[tx.tx_id] = tx

    return result


def load_kyc_profiles() -> dict[str, KYCProfile]:
    """
    Load KYC profiles — always uses synthetic data.

    IEEE-CIS does not contain KYC-equivalent data, so this always
    returns the Faker-generated profiles. In Phase 4+ this could
    be extended with identity verification service data.
    """
    if not KYC_PATH.exists():
        logger.warning(
            "kyc_profiles.json not found. "
            "Run: uv run python scripts/generate_data.py"
        )
        return {}

    with open(KYC_PATH) as f:
        raw = json.load(f)

    result = {}
    for item in raw:
        try:
            p = KYCProfile(**item)
            result[p.customer_id] = p
        except Exception as e:
            logger.warning(f"Skipping invalid KYC profile: {e}")

    return result


def data_source_status() -> dict:
    """
    Returns the current data source status.
    Used by the /health endpoint and dashboard.
    """
    ieee_available      = IEEE_PATH.exists()
    synthetic_available = SYNTHETIC_PATH.exists()
    kyc_available       = KYC_PATH.exists()

    active_source = "none"
    if ieee_available:
        active_source = "ieee_cis"
    elif synthetic_available:
        active_source = "synthetic"

    # Count records without loading full dataset
    ieee_count      = 0
    synthetic_count = 0

    if ieee_available:
        with open(IEEE_PATH) as f:
            ieee_count = len(json.load(f))

    if synthetic_available:
        with open(SYNTHETIC_PATH) as f:
            synthetic_count = len(json.load(f))

    return {
        "active_source"         : active_source,
        "ieee_cis_available"    : ieee_available,
        "ieee_cis_count"        : ieee_count,
        "synthetic_available"   : synthetic_available,
        "synthetic_count"       : synthetic_count,
        "kyc_available"         : kyc_available,
        "ieee_cis_path"         : str(IEEE_PATH),
        "download_instructions" : (
            "https://www.kaggle.com/competitions/ieee-fraud-detection/data"
            if not ieee_available else None
        ),
    }

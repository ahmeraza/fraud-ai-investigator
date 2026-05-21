"""
app/api/crypto.py
──────────────────
FastAPI router — crypto transaction monitoring endpoints.

Endpoints:
  POST /v1/crypto/screen          — screen one wallet address
  POST /v1/crypto/screen/batch    — screen multiple addresses
  GET  /v1/crypto/mixers          — list known sanctioned mixer addresses
  GET  /v1/crypto/status          — engine health and configuration

Design for Phase 4 compatibility:
  These endpoints are the human-facing interface to the same crypto
  screening logic that the LangGraph CryptoAgent will call programmatically
  in Phase 4. The agent will call CryptoAlertEngine.screen_address()
  directly (bypassing HTTP), but the response shapes are identical,
  so the Streamlit dashboard works without changes in Phase 6.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
from app.crypto.mixer_detector import SANCTIONED_MIXER_ADDRESSES

logger   = get_logger(__name__)
settings = get_settings()
router   = APIRouter()

_engine: Optional[CryptoAlertEngine] = None


def _get_engine() -> CryptoAlertEngine:
    """Lazy singleton — initialised on first request."""
    global _engine
    if _engine is None:
        if not settings.etherscan_api_key:
            raise HTTPException(
                status_code=503,
                detail=(
                    "ETHERSCAN_API_KEY not configured. "
                    "Add it to .env: get free key at https://etherscan.io/myapikey"
                ),
            )
        _engine = CryptoAlertEngine(
            etherscan_api_key = settings.etherscan_api_key,
            score_threshold   = settings.crypto_mixer_score_threshold,
        )
    return _engine


# ── Request / Response schemas ────────────────────────────────────────────────

class ScreenAddressRequest(BaseModel):
    address    : str = Field(description="Ethereum wallet address (0x...)")
    customer_id: str = Field(default="UNKNOWN", description="Internal customer ID")
    tx_id      : str = Field(default="CRYPTO-MANUAL")
    chain_id   : int = Field(default=1, description="Chain ID: 1=Ethereum, 137=Polygon")
    limit      : int = Field(default=100, le=1000, description="Max transactions to fetch")
    note       : str = Field(default="")


class BatchScreenRequest(BaseModel):
    addresses   : list[str] = Field(max_length=10, description="Up to 10 addresses")
    customer_id : str = Field(default="UNKNOWN")
    chain_id    : int = Field(default=1)
    limit       : int = Field(default=50, le=500)


# ── Validators ────────────────────────────────────────────────────────────────

def _validate_eth_address(address: str) -> str:
    """Basic Ethereum address format check."""
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid Ethereum address format: {address}. Expected 0x + 40 hex chars.",
        )
    return address.lower()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/screen",
    summary="Screen one wallet address for mixer usage",
    description=(
        "Fetches on-chain transaction history from Etherscan and runs "
        "3-layer mixer detection (known addresses + behavioural patterns + scoring). "
        "Creates an alert in the pipeline if risk score >= threshold (default 60)."
    ),
)
def screen_address(body: ScreenAddressRequest) -> dict:
    address = _validate_eth_address(body.address)
    engine  = _get_engine()

    request = CryptoScreeningRequest(
        address     = address,
        customer_id = body.customer_id,
        tx_id       = body.tx_id,
        chain_id    = body.chain_id,
        limit       = body.limit,
        note        = body.note,
    )

    response = engine.screen_address(request)

    if response.error:
        raise HTTPException(
            status_code=500,
            detail=f"Screening failed: {response.error}",
        )

    return response.to_dict()


@router.post(
    "/screen/batch",
    summary="Screen multiple wallet addresses",
    description=(
        "Screens up to 10 addresses sequentially. "
        "Rate limited to respect Etherscan free tier (5 req/sec). "
        "For large batches, use the screening script: "
        "uv run python scripts/screen_crypto_addresses.py"
    ),
)
def screen_batch(body: BatchScreenRequest) -> dict:
    if not body.addresses:
        raise HTTPException(status_code=422, detail="addresses list cannot be empty.")

    engine  = _get_engine()
    results = []

    for raw_addr in body.addresses:
        try:
            address = _validate_eth_address(raw_addr)
        except HTTPException:
            results.append({"address": raw_addr, "error": "Invalid address format"})
            continue

        request  = CryptoScreeningRequest(
            address    = address,
            customer_id= body.customer_id,
            chain_id   = body.chain_id,
            limit      = body.limit,
        )
        response = engine.screen_address(request)
        results.append(response.to_dict())

    flagged = sum(1 for r in results if r.get("screening", {}).get("is_flagged", False))

    return {
        "total_screened": len(results),
        "flagged"       : flagged,
        "clear"         : len(results) - flagged,
        "results"       : results,
    }


@router.get(
    "/mixers",
    summary="List known sanctioned mixer addresses",
    description=(
        "Returns the current list of OFAC-sanctioned mixer addresses "
        "used by Layer 1 detection. Updated as new sanctions are added."
    ),
)
def list_known_mixers() -> dict:
    return {
        "count"  : len(SANCTIONED_MIXER_ADDRESSES),
        "mixers" : [
            {
                "address"    : addr,
                "name"       : info["name"],
                "sanction"   : info["sanction"],
                "date_listed": info["date"],
                "notes"      : info["notes"],
            }
            for addr, info in SANCTIONED_MIXER_ADDRESSES.items()
        ],
    }


@router.get(
    "/status",
    summary="Crypto monitoring engine status",
)
def engine_status() -> dict:
    api_key_configured = bool(settings.etherscan_api_key)
    return {
        "etherscan_api_configured" : api_key_configured,
        "score_threshold"          : settings.crypto_mixer_score_threshold,
        "known_mixer_addresses"    : len(SANCTIONED_MIXER_ADDRESSES),
        "supported_chains"         : {
            str(k): v
            for k, v in {
                1: "Ethereum", 137: "Polygon", 8453: "Base",
                42161: "Arbitrum", 56: "BNB Chain",
            }.items()
        },
        "free_tier_limits"         : {
            "requests_per_second": 5,
            "requests_per_day"   : 100000,
            "records_per_request": 1000,
        },
        "get_api_key_url"          : "https://etherscan.io/myapikey" if not api_key_configured else None,
    }

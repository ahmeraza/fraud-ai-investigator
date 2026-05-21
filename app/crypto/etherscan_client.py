"""
app/crypto/etherscan_client.py
────────────────────────────────
Etherscan V2 API client for on-chain transaction data.

What is Etherscan?
  The primary block explorer for Ethereum and 60+ EVM-compatible chains.
  One API key covers all chains via the chainid parameter.

Why we need this:
  Traditional AML (Phases 1-3) only sees bank-rail transactions.
  A growing share of financial crime moves through crypto — mixers,
  darknet markets, and sanctions-evading wallet clusters. This client
  gives the alert engine on-chain visibility that traditional systems miss.

Free tier: 5 req/sec, 100k req/day, 1,000 records per request — $0/month.
Get key: https://etherscan.io/myapikey

Supported chains (same key, different chainid):
  1=Ethereum, 137=Polygon, 8453=Base, 42161=Arbitrum, 56=BNB Chain

Pipeline role:
  Called by CryptoAlertEngine.screen_address() which is called from
  the /v1/crypto/screen endpoint (Phase 3 OFAC branch) and will be
  called by the LangGraph CryptoAgent in Phase 4.
"""

from __future__ import annotations

import time
from typing import Optional

import requests

from app.core.logging import get_logger

logger = get_logger(__name__)

ETHERSCAN_V2_BASE = "https://api.etherscan.io/v2/api"

SUPPORTED_CHAINS = {
    1    : "Ethereum",
    137  : "Polygon",
    8453 : "Base",
    42161: "Arbitrum",
    56   : "BNB Chain",
}

# Free tier limit: 5 req/sec — we cap at 4 for safety margin
MIN_REQUEST_GAP = 0.25


class EtherscanClient:
    """
    Rate-limited Etherscan V2 API wrapper.

    Handles rate limiting, retry with exponential backoff,
    and normalises all responses to plain Python dicts/lists.
    No third-party blockchain libraries required.
    """

    def __init__(
        self,
        api_key : str,
        chain_id: int = 1,
        timeout : int = 15,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "Etherscan API key required. "
                "Get free key: https://etherscan.io/myapikey"
            )
        self._api_key  = api_key
        self._chain_id = chain_id
        self._timeout  = timeout
        self._last_req = 0.0

        chain_name = SUPPORTED_CHAINS.get(chain_id, f"chain-{chain_id}")
        logger.info(f"EtherscanClient ready | chain={chain_name} ({chain_id})")

    def _request(self, params: dict, max_retries: int = 3) -> list | str:
        """Rate-limited API call with retry logic."""
        elapsed = time.monotonic() - self._last_req
        if elapsed < MIN_REQUEST_GAP:
            time.sleep(MIN_REQUEST_GAP - elapsed)

        full_params = {"chainid": self._chain_id, "apikey": self._api_key, **params}
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                self._last_req = time.monotonic()
                resp = requests.get(ETHERSCAN_V2_BASE, params=full_params, timeout=self._timeout)
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") == "1":
                    return data["result"]

                if data.get("message") in ("No transactions found", "No records found"):
                    return []

                logger.warning(f"Etherscan API error | {data.get('message')} | params={params}")
                return []

            except requests.RequestException as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(f"Etherscan failed | attempt={attempt} | error={e} | retry_in={wait}s")
                if attempt < max_retries:
                    time.sleep(wait)

        logger.error(f"All Etherscan retries exhausted | {last_error}")
        return []

    def get_transactions(
        self,
        address    : str,
        start_block: int = 0,
        end_block  : int = 99999999,
        limit      : int = 100,
        sort       : str = "desc",
    ) -> list[dict]:
        """Normal ETH transactions for an address. Newest-first by default."""
        logger.info(f"Fetching transactions | {address[:10]}... | limit={limit}")
        result = self._request({
            "module": "account", "action": "txlist",
            "address": address, "startblock": start_block,
            "endblock": end_block, "page": 1,
            "offset": min(limit, 1000), "sort": sort,
        })
        txs = result if isinstance(result, list) else []
        logger.info(f"Transactions fetched | {address[:10]}... | count={len(txs)}")
        return txs

    def get_token_transfers(
        self,
        address      : str,
        contract_addr: Optional[str] = None,
        limit        : int = 100,
    ) -> list[dict]:
        """
        ERC-20 token transfer events for an address.
        USDT/USDC transfers are a key sanctions-evasion signal — stablecoins
        are liquid and move across borders without bank-rail visibility.
        """
        params: dict = {
            "module": "account", "action": "tokentx",
            "address": address, "page": 1,
            "offset": min(limit, 1000), "sort": "desc",
        }
        if contract_addr:
            params["contractaddress"] = contract_addr

        result = self._request(params)
        return result if isinstance(result, list) else []

    def get_eth_balance(self, address: str) -> float:
        """ETH balance in ether (converted from wei)."""
        result = self._request({"module": "account", "action": "balance", "address": address, "tag": "latest"})
        return int(result) / 1e18 if isinstance(result, str) else 0.0

    def get_internal_transactions(self, address: str, limit: int = 100) -> list[dict]:
        """
        Internal (contract-to-contract) transactions.
        High internal-to-normal tx ratio is a mixer usage indicator —
        mixers generate heavy contract interaction patterns.
        """
        result = self._request({
            "module": "account", "action": "txlistinternal",
            "address": address, "page": 1,
            "offset": min(limit, 1000), "sort": "desc",
        })
        return result if isinstance(result, list) else []

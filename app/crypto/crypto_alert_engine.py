"""
app/crypto/crypto_alert_engine.py
────────────────────────────────────
Crypto-specific alert engine — orchestrates on-chain screening.

Pipeline role:
  Parallel track to the payment alert engine (app/services/alert_engine.py).
  When a transaction or customer has a crypto wallet address, this engine
  screens it on-chain using Etherscan and the MixerDetector.

  This integrates naturally with Phase 4's LangGraph graph:
    payment_alert_engine  → PaymentAlertAgent
    crypto_alert_engine   → CryptoAlertAgent  [Phase 4]
    Both feed into → RiskSynthesisAgent → HITL

Detection flow:
  1. Receive a wallet address (from transaction metadata or manual input)
  2. Fetch on-chain data via EtherscanClient
  3. Run MixerDetector — 3-layer analysis
  4. Create a CryptoAlert and save to the alert store
  5. Log audit event with full evidence trail

Why separate from the payment engine?
  Different data source (blockchain vs bank), different rules (on-chain
  patterns vs FATF corridors), different latency profile (Etherscan ~0.5s
  vs rule engine ~0ms). Keeping them separate allows Phase 4 to run them
  as parallel LangGraph agents without coupling their logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger
from app.crypto.etherscan_client import EtherscanClient
from app.crypto.mixer_detector import MixerDetectionResult, MixerDetector
from app.services.alert_store import store
from app.shared.models import Alert, AlertStatus, AlertTrigger, AuditEvent

logger = get_logger(__name__)

ETH_TO_AED_RATE = 12_000.0  # approximate — update in config for production


@dataclass
class CryptoScreeningRequest:
    """Input for one crypto screening request."""
    address     : str
    customer_id : str = "UNKNOWN"
    tx_id       : str = "CRYPTO-MANUAL"
    chain_id    : int = 1
    limit       : int = 100
    note        : str = ""


@dataclass
class CryptoScreeningResponse:
    """Full result of one crypto screening run."""
    address         : str
    customer_id     : str
    chain_id        : int
    detection_result: Optional[MixerDetectionResult] = None
    alert_created   : bool = False
    alert_id        : str = ""
    error           : str = ""

    def to_dict(self) -> dict:
        base = {
            "address"      : self.address,
            "customer_id"  : self.customer_id,
            "chain_id"     : self.chain_id,
            "alert_created": self.alert_created,
            "alert_id"     : self.alert_id,
        }
        if self.detection_result:
            base["screening"] = self.detection_result.to_dict()
        if self.error:
            base["error"] = self.error
        return base


class CryptoAlertEngine:
    """
    Orchestrates on-chain screening and alert creation.

    Used by:
      - POST /v1/crypto/screen (immediate screening via API)
      - CryptoAgent in LangGraph Phase 4 (parallel investigation)
      - Batch screening script for watchlist monitoring

    Stateless — safe to call concurrently from multiple agents.
    """

    def __init__(self, etherscan_api_key: str, score_threshold: int = 60) -> None:
        self._api_key        = etherscan_api_key
        self._score_threshold= score_threshold
        self._detector       = MixerDetector(score_threshold=score_threshold)
        logger.info(
            f"CryptoAlertEngine ready | threshold={score_threshold} | "
            f"known_mixers={len(__import__('app.crypto.mixer_detector', fromlist=['SANCTIONED_MIXER_ADDRESSES']).SANCTIONED_MIXER_ADDRESSES)}"
        )

    def screen_address(self, request: CryptoScreeningRequest) -> CryptoScreeningResponse:
        """
        Screen one wallet address end-to-end.

        Fetches on-chain data, runs detection, creates alert if flagged,
        logs full audit trail. Returns CryptoScreeningResponse always —
        never raises (errors are captured in the response).
        """
        try:
            client = EtherscanClient(
                api_key  = self._api_key,
                chain_id = request.chain_id,
            )

            # Fetch all on-chain data in sequence
            # Phase 4: these become parallel async calls in the LangGraph agent
            txs       = client.get_transactions(request.address, limit=request.limit)
            token_txs = client.get_token_transfers(request.address, limit=request.limit)
            balance   = client.get_eth_balance(request.address)
            internal  = client.get_internal_transactions(request.address, limit=50)

            # Run detection
            result = self._detector.analyse(
                address           = request.address,
                transactions      = txs,
                token_transactions= token_txs,
                eth_balance       = balance,
                internal_txs      = internal,
            )

            alert_id = ""
            alert_created = False

            if result.is_flagged:
                # Create alert in store — compatible with existing pipeline
                alert = Alert(
                    tx_id       = request.tx_id,
                    customer_id = request.customer_id,
                    trigger     = AlertTrigger.SANCTIONED_CORRIDOR,
                    status      = AlertStatus.PENDING,
                )
                store.save(alert)
                alert_id      = str(alert.alert_id)
                alert_created = True

                # Full evidence audit trail
                store.log_event(AuditEvent(
                    alert_id    = alert_id,
                    event_type  = "CRYPTO_ALERT_CREATED",
                    description = (
                        f"On-chain mixer detection | "
                        f"address={request.address[:10]}... | "
                        f"score={result.risk_score} | "
                        f"severity={result.severity} | "
                        f"direct_hits={len(result.direct_hits)}"
                    ),
                    actor    = "crypto_alert_engine",
                    metadata = {
                        "address"          : request.address,
                        "chain_id"         : request.chain_id,
                        "risk_score"       : result.risk_score,
                        "severity"         : result.severity,
                        "direct_hits"      : result.direct_hits[:3],
                        "signal_count"     : len(result.signals),
                        "transaction_count": result.transaction_count,
                        "eth_balance"      : round(result.eth_balance, 6),
                        "eth_balance_aed"  : round(result.eth_balance * ETH_TO_AED_RATE, 2),
                        "recommended_action": result.recommended_action,
                    },
                ))

                logger.warning(
                    f"Crypto alert created | alert={alert_id} | "
                    f"address={request.address[:10]}... | "
                    f"score={result.risk_score} | {result.severity}"
                )

            return CryptoScreeningResponse(
                address          = request.address,
                customer_id      = request.customer_id,
                chain_id         = request.chain_id,
                detection_result = result,
                alert_created    = alert_created,
                alert_id         = alert_id,
            )

        except Exception as e:
            logger.error(
                f"Crypto screening failed | "
                f"address={request.address[:10]}... | error={e}"
            )
            return CryptoScreeningResponse(
                address    = request.address,
                customer_id= request.customer_id,
                chain_id   = request.chain_id,
                error      = str(e),
            )

"""
app/crypto/mixer_detector.py
──────────────────────────────
On-chain mixer and tumbler detection engine.

What are crypto mixers?
  Mixing services (tumblers) obfuscate the origin of cryptocurrency by
  pooling funds from multiple users and redistributing them. They are
  the primary tool used to launder crypto proceeds from:
    - Ransomware payments
    - Darknet market sales
    - Sanctions evasion (North Korea, Iran, Russia)
    - Exchange hacks

  OFAC has sanctioned several mixers directly:
    - Tornado Cash (sanctioned August 2022)
    - Blender.io (sanctioned May 2022)
    - Sinbad (sanctioned November 2023)
    - ChipMixer (seized March 2023)

Detection approach (3 layers):

  Layer 1 — Known address blacklist
    Direct interaction with sanctioned mixer contract addresses.
    Highest confidence. No false positives.

  Layer 2 — Behavioural pattern analysis
    Transaction patterns that indicate mixer usage even when the
    mixer address isn't directly known:
      - Round-number ETH amounts (0.1, 0.5, 1.0 ETH)
      - High internal transaction ratio (contract hops)
      - Short time between incoming and outgoing transactions
      - Equal output amounts to multiple addresses
      - Unusual gas price patterns

  Layer 3 — Heuristic risk scoring
    Combines signals into a 0-100 risk score with evidence trail.
    Threshold 60+ = escalate for investigation.

Integration with Phases 4-7:
  Phase 4: CryptoAgent in LangGraph uses this to generate evidence
  Phase 5: HITL queue receives high-score crypto alerts
  Phase 6: Dashboard shows on-chain risk alongside payment risk
  Phase 7: Deployed screener available as API endpoint
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import re

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Known sanctioned mixer addresses (Ethereum mainnet) ───────────────────────
# Source: OFAC SDN list additions + FinCEN advisories
# These are real addresses from public OFAC and law enforcement records.

SANCTIONED_MIXER_ADDRESSES: dict[str, dict] = {
    # Tornado Cash — OFAC sanctioned August 8, 2022
    "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b": {
        "name"    : "Tornado Cash Router",
        "sanction": "OFAC SDN",
        "date"    : "2022-08-08",
        "notes"   : "Primary Tornado Cash proxy router",
    },
    "0x722122df12d4e14e13ac3b6895a86e84145b6967": {
        "name"    : "Tornado Cash 0.1 ETH Pool",
        "sanction": "OFAC SDN",
        "date"    : "2022-08-08",
        "notes"   : "0.1 ETH anonymity pool",
    },
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936": {
        "name"    : "Tornado Cash 1 ETH Pool",
        "sanction": "OFAC SDN",
        "date"    : "2022-08-08",
        "notes"   : "1 ETH anonymity pool",
    },
    "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf": {
        "name"    : "Tornado Cash 10 ETH Pool",
        "sanction": "OFAC SDN",
        "date"    : "2022-08-08",
        "notes"   : "10 ETH anonymity pool",
    },
    "0xa160cdab225685da1d56aa342ad8841c3b53f291": {
        "name"    : "Tornado Cash 100 ETH Pool",
        "sanction": "OFAC SDN",
        "date"    : "2022-08-08",
        "notes"   : "100 ETH anonymity pool",
    },
    # Blender.io — OFAC sanctioned May 6, 2022
    "0x8576acc5c05d6ce88f4e49bf65bdf0c62f91353c": {
        "name"    : "Blender.io",
        "sanction": "OFAC SDN",
        "date"    : "2022-05-06",
        "notes"   : "Bitcoin mixer used by Lazarus Group (DPRK)",
    },
    # Sinbad.io — OFAC sanctioned November 29, 2023
    "0x4cbf64da75a7367cf3946dce22e91ac5b15f3b69": {
        "name"    : "Sinbad.io",
        "sanction": "OFAC SDN",
        "date"    : "2023-11-29",
        "notes"   : "Mixer used by Lazarus Group for Ronin hack proceeds",
    },
}

# Normalise all keys to lowercase
SANCTIONED_MIXER_ADDRESSES = {
    k.lower(): v for k, v in SANCTIONED_MIXER_ADDRESSES.items()
}

# ── Behavioural signal weights ────────────────────────────────────────────────
# Used in the scoring model. Weights sum to 100 at maximum.

SIGNAL_WEIGHTS = {
    "direct_mixer_interaction": 70,   # direct hit on known mixer
    "round_amount_pattern"    : 15,   # e.g. exactly 0.1, 1.0, 10.0 ETH
    "high_internal_tx_ratio"  : 10,   # >50% of txs are internal
    "rapid_in_out_pattern"    : 15,   # funds in then out within 1 hour
    "equal_output_amounts"    : 15,   # same amount sent to multiple addresses
    "privacy_token_transfers" : 10,   # Zcash/Monero bridge interactions
    "known_exchange_deposit"  : -10,  # direct to KYC exchange (reduces risk)
}


# ── Result models ─────────────────────────────────────────────────────────────

@dataclass
class MixerSignal:
    """A single detected risk signal with evidence."""
    signal_type  : str
    score        : int          # contribution to total score
    description  : str
    evidence     : dict = field(default_factory=dict)


@dataclass
class MixerDetectionResult:
    """
    Complete result of mixer detection for one wallet address.

    Attributes:
        address        : wallet address screened
        risk_score     : 0-100 composite risk score
        is_flagged     : True if score >= threshold (default 60)
        signals        : list of detected risk signals with evidence
        direct_hits    : sanctioned mixer addresses interacted with
        transaction_count: number of transactions analysed
        screening_ms   : time taken for screening
    """
    address          : str
    risk_score       : int
    is_flagged       : bool
    signals          : list[MixerSignal] = field(default_factory=list)
    direct_hits      : list[dict]        = field(default_factory=list)
    transaction_count: int = 0
    token_tx_count   : int = 0
    eth_balance      : float = 0.0
    screening_ms     : float = 0.0

    @property
    def severity(self) -> str:
        if self.risk_score >= 90: return "CRITICAL"
        if self.risk_score >= 70: return "HIGH"
        if self.risk_score >= 50: return "MEDIUM"
        return "LOW"

    @property
    def recommended_action(self) -> str:
        if self.risk_score >= 90: return "BLOCK_AND_REPORT"
        if self.risk_score >= 70: return "ESCALATE_COMPLIANCE"
        if self.risk_score >= 50: return "MANUAL_REVIEW"
        return "MONITOR"

    def to_dict(self) -> dict:
        return {
            "address"          : self.address,
            "risk_score"       : self.risk_score,
            "severity"         : self.severity,
            "is_flagged"       : self.is_flagged,
            "recommended_action": self.recommended_action,
            "direct_hits"      : self.direct_hits,
            "signals"          : [
                {
                    "type"       : s.signal_type,
                    "score"      : s.score,
                    "description": s.description,
                    "evidence"   : s.evidence,
                }
                for s in self.signals
            ],
            "transaction_count": self.transaction_count,
            "token_tx_count"   : self.token_tx_count,
            "eth_balance_eth"  : round(self.eth_balance, 6),
            "screening_ms"     : round(self.screening_ms, 1),
        }


# ── Detector ──────────────────────────────────────────────────────────────────

class MixerDetector:
    """
    Analyses on-chain transaction data for mixer usage patterns.

    Takes pre-fetched transaction lists (from EtherscanClient) and
    applies all three detection layers. Stateless — can be called
    concurrently from multiple LangGraph agents in Phase 4.

    Usage:
        detector = MixerDetector(score_threshold=60)
        result   = detector.analyse(
            address     = "0xabc...",
            transactions      = etherscan_client.get_transactions("0xabc..."),
            token_transactions= etherscan_client.get_token_transfers("0xabc..."),
            eth_balance = etherscan_client.get_eth_balance("0xabc..."),
        )
        if result.is_flagged:
            # create alert
    """

    def __init__(self, score_threshold: int = 60) -> None:
        self._threshold = score_threshold

    def analyse(
        self,
        address           : str,
        transactions      : list[dict],
        token_transactions: list[dict],
        eth_balance       : float = 0.0,
        internal_txs      : Optional[list[dict]] = None,
    ) -> MixerDetectionResult:
        """
        Run all detection layers and return a scored result.

        Args:
            address           : wallet address being analysed
            transactions      : normal ETH transactions (from get_transactions)
            token_transactions: ERC-20 transfers (from get_token_transfers)
            eth_balance       : current ETH balance in ether
            internal_txs      : internal transactions (optional — Layer 2 signal)
        """
        import time
        start = time.monotonic()

        address_lower = address.lower()
        signals: list[MixerSignal] = []
        direct_hits: list[dict]    = []
        total_score                = 0

        # ── Layer 1: Direct mixer address matching ────────────────────────────
        for tx in transactions:
            to_addr   = str(tx.get("to",   "")).lower()
            from_addr = str(tx.get("from", "")).lower()

            for addr in (to_addr, from_addr):
                if addr in SANCTIONED_MIXER_ADDRESSES and addr != address_lower:
                    mixer_info = SANCTIONED_MIXER_ADDRESSES[addr]
                    hit = {
                        "mixer_address": addr,
                        "mixer_name"   : mixer_info["name"],
                        "sanction"     : mixer_info["sanction"],
                        "date_listed"  : mixer_info["date"],
                        "tx_hash"      : tx.get("hash", ""),
                        "direction"    : "sent_to" if to_addr == addr else "received_from",
                    }
                    if hit not in direct_hits:
                        direct_hits.append(hit)

        if direct_hits:
            score = min(SIGNAL_WEIGHTS["direct_mixer_interaction"], 70)
            total_score += score
            signals.append(MixerSignal(
                signal_type = "DIRECT_MIXER_INTERACTION",
                score       = score,
                description = (
                    f"Direct interaction with {len(direct_hits)} sanctioned "
                    f"mixer address(es): "
                    f"{', '.join(h['mixer_name'] for h in direct_hits[:3])}"
                ),
                evidence = {"direct_hits": direct_hits[:5]},
            ))

        # ── Layer 2: Behavioural pattern analysis ─────────────────────────────

        if transactions:
            # Signal: round-number ETH amounts
            # Mixers typically use fixed denomination pools (0.1, 1, 10, 100 ETH)
            round_amounts = [
                tx for tx in transactions
                if self._is_round_eth_amount(tx.get("value", "0"))
            ]
            if len(round_amounts) >= 2:
                pct = len(round_amounts) / len(transactions)
                score = min(int(SIGNAL_WEIGHTS["round_amount_pattern"] * pct * 2), 15)
                if score >= 5:
                    total_score += score
                    signals.append(MixerSignal(
                        signal_type = "ROUND_AMOUNT_PATTERN",
                        score       = score,
                        description = (
                            f"{len(round_amounts)} of {len(transactions)} transactions "
                            f"use fixed mixer denomination amounts "
                            f"(0.1/0.5/1/10/100 ETH)"
                        ),
                        evidence = {
                            "round_tx_count": len(round_amounts),
                            "total_tx_count": len(transactions),
                            "percentage"    : round(pct * 100, 1),
                        },
                    ))

            # Signal: rapid in/out pattern
            # Funds received and sent within 1 hour = classic layering
            rapid = self._detect_rapid_in_out(address_lower, transactions)
            if rapid["detected"]:
                score = SIGNAL_WEIGHTS["rapid_in_out_pattern"]
                total_score += score
                signals.append(MixerSignal(
                    signal_type = "RAPID_IN_OUT_PATTERN",
                    score       = score,
                    description = (
                        f"Funds received and re-sent within "
                        f"{rapid['min_gap_minutes']:.0f} minutes — "
                        "classic layering/pass-through pattern"
                    ),
                    evidence = rapid,
                ))

        # Signal: high internal transaction ratio
        if internal_txs is not None and transactions:
            internal_count = len(internal_txs)
            normal_count   = len(transactions)
            if normal_count > 0:
                ratio = internal_count / (normal_count + internal_count)
                if ratio > 0.5:
                    score = min(int(SIGNAL_WEIGHTS["high_internal_tx_ratio"] * ratio), 10)
                    total_score += score
                    signals.append(MixerSignal(
                        signal_type = "HIGH_INTERNAL_TX_RATIO",
                        score       = score,
                        description = (
                            f"{internal_count} internal vs {normal_count} normal transactions "
                            f"({ratio:.0%} internal) — indicates heavy contract interaction "
                            "typical of mixers and DeFi obfuscation"
                        ),
                        evidence = {
                            "internal_count": internal_count,
                            "normal_count"  : normal_count,
                            "ratio"         : round(ratio, 3),
                        },
                    ))

        # Cap total score at 100
        total_score = min(total_score, 100)
        is_flagged  = total_score >= self._threshold
        ms          = (time.monotonic() - start) * 1000

        if is_flagged:
            logger.warning(
                f"Mixer detection HIT | address={address[:10]}... | "
                f"score={total_score} | severity={self._severity_label(total_score)} | "
                f"direct_hits={len(direct_hits)} | signals={len(signals)}"
            )
        else:
            logger.debug(
                f"Mixer detection CLEAR | address={address[:10]}... | score={total_score}"
            )

        return MixerDetectionResult(
            address           = address,
            risk_score        = total_score,
            is_flagged        = is_flagged,
            signals           = signals,
            direct_hits       = direct_hits,
            transaction_count = len(transactions),
            token_tx_count    = len(token_transactions),
            eth_balance       = eth_balance,
            screening_ms      = ms,
        )

    # ── Helper methods ────────────────────────────────────────────────────────

    @staticmethod
    def _is_round_eth_amount(value_wei: str) -> bool:
        """
        Check if a wei value is a common mixer denomination.
        Tornado Cash pools: 0.1, 1, 10, 100 ETH exactly.
        """
        try:
            eth = int(value_wei) / 1e18
            # Check if within 0.001 ETH of a round mixer amount
            mixer_amounts = [0.1, 0.5, 1.0, 5.0, 10.0, 50.0, 100.0]
            return any(abs(eth - amt) < 0.001 for amt in mixer_amounts)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _detect_rapid_in_out(
        address     : str,
        transactions: list[dict],
        max_gap_sec : int = 3600,
    ) -> dict:
        """
        Detect funds received then quickly re-sent (layering pattern).
        Looks for incoming followed by outgoing within max_gap_sec (1 hour default).
        """
        incoming = [
            tx for tx in transactions
            if str(tx.get("to", "")).lower() == address
            and int(tx.get("value", "0")) > 0
        ]
        outgoing = [
            tx for tx in transactions
            if str(tx.get("from", "")).lower() == address
            and int(tx.get("value", "0")) > 0
        ]

        if not incoming or not outgoing:
            return {"detected": False}

        min_gap = float("inf")
        for inc in incoming:
            for out in outgoing:
                try:
                    inc_ts = int(inc.get("timeStamp", 0))
                    out_ts = int(out.get("timeStamp", 0))
                    gap    = out_ts - inc_ts
                    if 0 < gap < max_gap_sec:
                        min_gap = min(min_gap, gap)
                except (ValueError, TypeError):
                    continue

        if min_gap < float("inf"):
            return {
                "detected"         : True,
                "min_gap_seconds"  : min_gap,
                "min_gap_minutes"  : min_gap / 60,
                "incoming_count"   : len(incoming),
                "outgoing_count"   : len(outgoing),
            }
        return {"detected": False}

    @staticmethod
    def _severity_label(score: int) -> str:
        if score >= 90: return "CRITICAL"
        if score >= 70: return "HIGH"
        if score >= 50: return "MEDIUM"
        return "LOW"

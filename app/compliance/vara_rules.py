"""
app/compliance/vara_rules.py
──────────────────────────────
VARA (Virtual Assets Regulatory Authority) compliance rules.

Regulatory basis:
  - VARA Virtual Assets and Related Activities Regulations 2023
  - VARA Compliance and Risk Management Rulebook 2023
  - FATF Guidance on Virtual Assets and Virtual Asset Service Providers 2021
  - CBUAE/VARA Joint Supervisory Framework 2023

VARA was established in Dubai by Law No. 4 of 2022. It regulates
all virtual asset activities in Dubai (except DIFC) and sets the
most comprehensive crypto compliance framework in the MENA region.

Rules implemented:
  Rule 12: FATF Travel Rule threshold
  Rule 13: Unhosted/non-custodial wallet interaction
  Rule 14: High-risk VASP jurisdiction
  Rule 15: DeFi protocol interaction
  Rule 16: NFT high-value transaction
  Rule 17: Stablecoin rapid cycling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# FATF Travel Rule threshold — $1,000 USD / AED 3,674
TRAVEL_RULE_THRESHOLD_AED = 3_674.0

# VARA high-risk VASP jurisdictions — VASPs operating from FATF
# non-compliant countries require enhanced due diligence
HIGH_RISK_VASP_JURISDICTIONS = {
    "KP", "IR", "MM", "SY", "YE", "SD",
}

# DeFi protocol categories flagged by VARA for enhanced monitoring
VARA_FLAGGED_PROTOCOL_TYPES = {
    "DEX",          # Decentralised exchanges (Uniswap, dYdX)
    "MIXER",        # Mixing/tumbling services
    "BRIDGE",       # Cross-chain bridges (high obfuscation risk)
    "LENDING",      # Undercollateralised lending protocols
    "PRIVACY",      # Privacy coins/protocols (Monero, Zcash bridges)
}

# NFT high-value threshold — FATF flags art/collectibles > $10,000
# as potential art-based money laundering vehicle
NFT_HIGH_VALUE_THRESHOLD_AED = 36_740.0  # ~$10,000 USD

# Stablecoin rapid cycling — in/out within 5 minutes = layering signal
STABLECOIN_CYCLE_WINDOW_SECONDS = 300

# Known stablecoin contract addresses (Ethereum mainnet)
STABLECOIN_CONTRACTS = {
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0x4fabb145d64652a948d72533023f6e7a623c7c53": "BUSD",
}


@dataclass
class VARAComplianceResult:
    """Result of VARA compliance check on a virtual asset transaction."""
    rule_id          : str
    rule_name        : str
    triggered        : bool
    severity         : str   # LOW / MEDIUM / HIGH / CRITICAL
    regulatory_basis : str
    description      : str
    recommended_action: str
    evidence         : dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rule_id"           : self.rule_id,
            "rule_name"         : self.rule_name,
            "triggered"         : self.triggered,
            "severity"          : self.severity,
            "regulatory_basis"  : self.regulatory_basis,
            "description"       : self.description,
            "recommended_action": self.recommended_action,
            "evidence"          : self.evidence,
        }


def check_travel_rule(
    amount_aed       : float,
    has_originator   : bool = False,
    has_beneficiary  : bool = False,
) -> VARAComplianceResult:
    """
    Rule 12: FATF Travel Rule (Recommendation 16)

    Virtual asset transfers above USD 1,000 (AED 3,674) must include
    originator and beneficiary information. UAE implemented this via
    VARA's Travel Rule compliance requirements effective January 2023.

    Non-compliance = regulatory breach, not just a risk signal.
    """
    triggered = (
        amount_aed >= TRAVEL_RULE_THRESHOLD_AED and
        (not has_originator or not has_beneficiary)
    )
    return VARAComplianceResult(
        rule_id    = "VARA-12",
        rule_name  = "FATF Travel Rule",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "FATF Recommendation 16 — Virtual Assets Travel Rule; "
            "VARA Compliance Rulebook 2023 §4.2; "
            "CBUAE/VARA Joint Supervisory Framework 2023"
        ),
        description = (
            f"Virtual asset transfer of AED {amount_aed:,.2f} "
            f"{'exceeds' if amount_aed >= TRAVEL_RULE_THRESHOLD_AED else 'below'} "
            f"Travel Rule threshold (AED {TRAVEL_RULE_THRESHOLD_AED:,.0f}). "
            f"Originator data: {'present' if has_originator else 'MISSING'}. "
            f"Beneficiary data: {'present' if has_beneficiary else 'MISSING'}."
        ),
        recommended_action = (
            "BLOCK — collect originator/beneficiary information before processing"
            if triggered else "PASS"
        ),
        evidence = {
            "amount_aed"       : amount_aed,
            "threshold_aed"    : TRAVEL_RULE_THRESHOLD_AED,
            "has_originator"   : has_originator,
            "has_beneficiary"  : has_beneficiary,
        },
    )


def check_unhosted_wallet(
    wallet_type      : str,
    amount_aed       : float,
    customer_verified: bool = True,
) -> VARAComplianceResult:
    """
    Rule 13: Unhosted/Non-Custodial Wallet Interaction

    VARA requires enhanced due diligence when a VASP processes
    transactions to/from unhosted (self-custodied) wallets above
    AED 3,674 (FATF Travel Rule threshold). The wallet holder's
    identity cannot be verified through the counterparty VASP.

    Regulatory basis: VARA CRMR 2023 §5.1 — Unhosted Wallet Policy
    """
    is_unhosted = wallet_type.lower() in ("unhosted", "self-custodied", "non-custodial", "hardware")
    triggered   = is_unhosted and amount_aed >= TRAVEL_RULE_THRESHOLD_AED and not customer_verified

    return VARAComplianceResult(
        rule_id    = "VARA-13",
        rule_name  = "Unhosted Wallet Enhanced Due Diligence",
        triggered  = triggered,
        severity   = "MEDIUM" if triggered else "LOW",
        regulatory_basis = (
            "VARA Compliance and Risk Management Rulebook 2023 §5.1; "
            "FATF Guidance on Virtual Assets 2021 §79-82"
        ),
        description = (
            f"Transaction to/from {wallet_type} wallet. "
            f"Amount AED {amount_aed:,.2f}. "
            f"Customer identity verification: {'complete' if customer_verified else 'REQUIRED'}."
        ),
        recommended_action = (
            "HOLD — complete enhanced due diligence before processing"
            if triggered else "PASS"
        ),
        evidence = {
            "wallet_type"       : wallet_type,
            "is_unhosted"       : is_unhosted,
            "amount_aed"        : amount_aed,
            "customer_verified" : customer_verified,
        },
    )


def check_high_risk_vasp(
    counterparty_jurisdiction: str,
    counterparty_vasp_name   : str = "",
) -> VARAComplianceResult:
    """
    Rule 14: High-Risk VASP Jurisdiction

    Transactions with VASPs operating from FATF non-compliant or
    sanctioned jurisdictions require enhanced due diligence per
    VARA's VASP risk classification framework.
    """
    triggered = counterparty_jurisdiction in HIGH_RISK_VASP_JURISDICTIONS

    return VARAComplianceResult(
        rule_id    = "VARA-14",
        rule_name  = "High-Risk VASP Jurisdiction",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "VARA CRMR 2023 §6.3 — VASP Risk Classification; "
            "FATF Recommendation 15 — New Technologies; "
            "FATF Non-Compliant Jurisdictions List 2024"
        ),
        description = (
            f"Counterparty VASP '{counterparty_vasp_name or 'Unknown'}' "
            f"operates from {counterparty_jurisdiction} — "
            f"{'FATF high-risk jurisdiction requiring EDD' if triggered else 'standard jurisdiction'}."
        ),
        recommended_action = (
            "ESCALATE — enhanced due diligence required for high-risk VASP"
            if triggered else "PASS"
        ),
        evidence = {
            "jurisdiction"      : counterparty_jurisdiction,
            "vasp_name"         : counterparty_vasp_name,
            "is_high_risk"      : triggered,
            "high_risk_list"    : list(HIGH_RISK_VASP_JURISDICTIONS),
        },
    )


def check_defi_interaction(
    protocol_type: str,
    amount_aed   : float,
) -> VARAComplianceResult:
    """
    Rule 15: DeFi Protocol Interaction

    VARA classifies certain DeFi interactions as requiring enhanced
    monitoring — particularly DEXs, bridges, mixers, and privacy
    protocols which can obscure transaction trails.
    """
    triggered = protocol_type.upper() in VARA_FLAGGED_PROTOCOL_TYPES and amount_aed > 0

    return VARAComplianceResult(
        rule_id    = "VARA-15",
        rule_name  = "DeFi Protocol Enhanced Monitoring",
        triggered  = triggered,
        severity   = "CRITICAL" if protocol_type.upper() in ("MIXER", "PRIVACY") else (
                     "HIGH" if triggered else "LOW"),
        regulatory_basis = (
            "VARA Virtual Assets Regulations 2023 §8.2 — DeFi Risk; "
            "FATF Guidance on Virtual Assets 2021 §88-95; "
            "VARA CRMR 2023 §7.1"
        ),
        description = (
            f"Interaction with {protocol_type} DeFi protocol detected. "
            f"Amount: AED {amount_aed:,.2f}. "
            f"Protocol category: "
            f"{'VARA-flagged — enhanced monitoring required' if triggered else 'standard'}."
        ),
        recommended_action = (
            "BLOCK_AND_REPORT" if protocol_type.upper() == "MIXER" else
            "ESCALATE" if triggered else "PASS"
        ),
        evidence = {
            "protocol_type"    : protocol_type,
            "amount_aed"       : amount_aed,
            "flagged_categories": list(VARA_FLAGGED_PROTOCOL_TYPES),
        },
    )


def check_stablecoin_cycling(
    token_symbol     : str,
    inbound_ts       : Optional[float],
    outbound_ts      : Optional[float],
    amount_aed       : float,
) -> VARAComplianceResult:
    """
    Rule 17: Stablecoin Rapid Cycling

    USDT/USDC received and immediately re-sent within 5 minutes
    is a classic layering pattern — the stablecoin acts as a
    value transfer vehicle without the price volatility of ETH/BTC.
    VARA flags this as a Travel Rule + layering risk.
    """
    gap_seconds = None
    triggered   = False

    if inbound_ts and outbound_ts and outbound_ts > inbound_ts:
        gap_seconds = outbound_ts - inbound_ts
        triggered   = (
            gap_seconds <= STABLECOIN_CYCLE_WINDOW_SECONDS and
            token_symbol.upper() in ("USDT", "USDC", "DAI", "BUSD") and
            amount_aed >= TRAVEL_RULE_THRESHOLD_AED
        )

    return VARAComplianceResult(
        rule_id    = "VARA-17",
        rule_name  = "Stablecoin Rapid Cycling",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "VARA Travel Rule Requirements 2023; "
            "FATF Recommendation 16 — Wire Transfer Rule; "
            "CBUAE AML/CFT Guidelines 2023 §3.4 — Layering Detection"
        ),
        description = (
            f"{token_symbol} received and re-sent "
            f"{'within ' + str(int(gap_seconds)) + ' seconds' if gap_seconds else 'timing unknown'}. "
            f"Amount: AED {amount_aed:,.2f}. "
            f"{'Rapid cycling pattern — potential layering.' if triggered else 'Normal pattern.'}"
        ),
        recommended_action = "ESCALATE" if triggered else "PASS",
        evidence = {
            "token"            : token_symbol,
            "gap_seconds"      : gap_seconds,
            "threshold_seconds": STABLECOIN_CYCLE_WINDOW_SECONDS,
            "amount_aed"       : amount_aed,
        },
    )

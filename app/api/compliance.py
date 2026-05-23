"""
app/api/compliance.py
──────────────────────
FastAPI router — compliance engine endpoints.

Endpoints:
  GET  /v1/compliance/rules           — list all 20 rules with regulatory basis
  POST /v1/compliance/check/payment   — run payment AML rules 6-11
  POST /v1/compliance/check/vara      — run VARA virtual asset rules 12-17
  GET  /v1/compliance/travel-rule     — Travel Rule threshold and status
  GET  /v1/compliance/vara/status     — VARA compliance framework status

Route ordering: static routes before parameterised routes.
"""

from __future__ import annotations

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.compliance.compliance_engine import ALL_RULES, ComplianceEngine
from app.compliance.vara_rules import (
    TRAVEL_RULE_THRESHOLD_AED,
    HIGH_RISK_VASP_JURISDICTIONS,
    VARA_FLAGGED_PROTOCOL_TYPES,
    STABLECOIN_CYCLE_WINDOW_SECONDS,
)
from app.compliance.payment_rules import (
    STRUCTURING_THRESHOLD_AED,
    STRUCTURING_BUFFER_AED,
    CROSS_BORDER_THRESHOLD_AED,
    DORMANT_DAYS,
    HIGH_RISK_MCC_CODES,
)
from app.core.logging import get_logger

logger  = get_logger(__name__)
router  = APIRouter()
_engine = ComplianceEngine()


# ── Request schemas ───────────────────────────────────────────────────────────

class PaymentComplianceRequest(BaseModel):
    transaction_id          : str   = Field(description="Transaction identifier")
    customer_id             : str   = Field(description="Customer identifier")
    amount_aed              : float = Field(gt=0, description="Transaction amount in AED")
    is_international        : bool  = False
    is_pep                  : bool  = False
    pep_category            : str   = ""
    days_since_last_activity: int   = Field(default=30, ge=0)
    mcc_code                : str   = Field(default="", description="Merchant Category Code")
    recent_amounts_aed      : list[float] = Field(default_factory=list)
    transaction_timestamps  : list[float] = Field(default_factory=list)


class VARAComplianceRequest(BaseModel):
    transaction_id           : str   = Field(description="Transaction identifier")
    customer_id              : str   = Field(description="Customer identifier")
    amount_aed               : float = Field(gt=0)
    has_originator           : bool  = False
    has_beneficiary          : bool  = False
    wallet_type              : str   = Field(default="hosted", description="hosted / unhosted / hardware")
    customer_verified        : bool  = True
    counterparty_jurisdiction: str   = ""
    counterparty_vasp        : str   = ""
    protocol_type            : str   = Field(default="", description="DEX / MIXER / BRIDGE / LENDING / PRIVACY")
    token_symbol             : str   = Field(default="", description="USDT / USDC / DAI / ETH / BTC")
    inbound_ts               : Optional[float] = None
    outbound_ts              : Optional[float] = None


# ── Endpoints — static routes first ──────────────────────────────────────────

@router.get(
    "/rules",
    summary="List all 20 compliance rules with regulatory basis",
    description=(
        "Returns all rules implemented in the compliance engine — "
        "5 base alert rules + 6 extended payment AML + 6 VARA virtual asset rules. "
        "Each rule includes its regulatory citation."
    ),
)
def list_rules() -> dict:
    return {
        "total_rules"  : len(ALL_RULES),
        "base_rules"   : 5,
        "payment_rules": 6,
        "vara_rules"   : 6,
        "rules"        : ALL_RULES,
        "regulatory_frameworks": [
            "CBUAE AML/CFT Guidelines 2023",
            "UAE Federal Law No. 20 of 2014 on AML/CFT",
            "FATF Recommendations 10, 12, 15, 16 (2023)",
            "VARA Virtual Assets Regulations 2023",
            "VARA Compliance & Risk Management Rulebook 2023",
            "FATF Guidance on Virtual Assets and VASPs 2021",
            "OFAC SDN Regulations",
            "UAE Cabinet Decision No. 10 of 2019",
        ],
    }


@router.get(
    "/travel-rule",
    summary="FATF Travel Rule threshold and implementation status",
)
def travel_rule_status() -> dict:
    return {
        "rule"               : "FATF Recommendation 16 — Travel Rule",
        "threshold_usd"      : 1_000,
        "threshold_aed"      : TRAVEL_RULE_THRESHOLD_AED,
        "uae_implementation" : "VARA Travel Rule Requirements — effective January 2023",
        "required_fields"    : [
            "originator_name",
            "originator_account",
            "originator_address",
            "beneficiary_name",
            "beneficiary_account",
        ],
        "applies_to"         : "All virtual asset transfers above threshold",
        "non_compliance"     : "Transaction must be blocked until data collected",
        "regulatory_basis"   : "VARA CRMR 2023 §4.2; FATF R.16",
    }


@router.get(
    "/vara/status",
    summary="VARA compliance framework status and configuration",
)
def vara_status() -> dict:
    return {
        "framework"              : "VARA Virtual Assets Regulatory Authority — Dubai",
        "established"            : "Dubai Law No. 4 of 2022",
        "jurisdiction"           : "Dubai (excluding DIFC)",
        "rules_implemented"      : 6,
        "travel_rule_threshold_aed": TRAVEL_RULE_THRESHOLD_AED,
        "high_risk_jurisdictions": sorted(HIGH_RISK_VASP_JURISDICTIONS),
        "flagged_protocol_types" : sorted(VARA_FLAGGED_PROTOCOL_TYPES),
        "stablecoin_cycle_window_seconds": STABLECOIN_CYCLE_WINDOW_SECONDS,
        "key_rulebooks"          : [
            "VARA Virtual Assets and Related Activities Regulations 2023",
            "VARA Compliance and Risk Management Rulebook 2023",
            "VARA Technology and Information Rulebook 2023",
        ],
    }


@router.get(
    "/payment/config",
    summary="Payment AML rules configuration and thresholds",
)
def payment_config() -> dict:
    return {
        "rules_implemented"         : 6,
        "structuring_threshold_aed" : STRUCTURING_THRESHOLD_AED,
        "structuring_buffer_aed"    : STRUCTURING_BUFFER_AED,
        "cross_border_threshold_aed": CROSS_BORDER_THRESHOLD_AED,
        "dormant_account_days"      : DORMANT_DAYS,
        "high_risk_mcc_codes"       : HIGH_RISK_MCC_CODES,
        "regulatory_basis"          : [
            "CBUAE AML/CFT Guidelines 2023",
            "FATF Recommendations 10, 12",
            "UAE Federal Law No. 20 of 2014",
        ],
    }


# ── Check endpoints ───────────────────────────────────────────────────────────

@router.post(
    "/check/payment",
    summary="Run payment AML compliance checks (Rules 6-11)",
    description=(
        "Checks a payment transaction against 6 extended AML rules: "
        "structuring detection, velocity, cross-border high-value, "
        "PEP screening, dormant account, and high-risk merchant MCC. "
        "Returns triggered rules with regulatory citations."
    ),
)
def check_payment(body: PaymentComplianceRequest) -> dict:
    report = _engine.check_payment_transaction(
        transaction_id           = body.transaction_id,
        customer_id              = body.customer_id,
        amount_aed               = body.amount_aed,
        is_international         = body.is_international,
        is_pep                   = body.is_pep,
        pep_category             = body.pep_category,
        days_since_last_activity = body.days_since_last_activity,
        mcc_code                 = body.mcc_code,
        recent_amounts_aed       = body.recent_amounts_aed,
        transaction_timestamps   = body.transaction_timestamps,
    )
    return report.to_dict()


@router.post(
    "/check/vara",
    summary="Run VARA virtual asset compliance checks (Rules 12-17)",
    description=(
        "Checks a virtual asset transaction against 6 VARA rules: "
        "FATF Travel Rule, unhosted wallet EDD, high-risk VASP, "
        "DeFi protocol monitoring, and stablecoin rapid cycling. "
        "Returns triggered rules with VARA regulatory citations."
    ),
)
def check_vara(body: VARAComplianceRequest) -> dict:
    report = _engine.check_virtual_asset_transaction(
        transaction_id            = body.transaction_id,
        customer_id               = body.customer_id,
        amount_aed                = body.amount_aed,
        has_originator            = body.has_originator,
        has_beneficiary           = body.has_beneficiary,
        wallet_type               = body.wallet_type,
        customer_verified         = body.customer_verified,
        counterparty_jurisdiction = body.counterparty_jurisdiction,
        counterparty_vasp         = body.counterparty_vasp,
        protocol_type             = body.protocol_type,
        token_symbol              = body.token_symbol,
        inbound_ts                = body.inbound_ts,
        outbound_ts               = body.outbound_ts,
    )
    return report.to_dict()

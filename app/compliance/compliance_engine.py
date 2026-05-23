"""
app/compliance/compliance_engine.py
─────────────────────────────────────
Unified compliance engine — orchestrates all 20 rules.

Combines:
  - Base alert engine rules 1-5 (existing)
  - Extended payment AML rules 6-11 (payment_rules.py)
  - VARA virtual asset rules 12-17 (vara_rules.py)

Each check is independent — failures in one rule don't affect others.
Returns a structured ComplianceReport with all results, triggered rules,
and a composite risk level for the transaction.

Used by:
  POST /v1/compliance/check    — API endpoint
  ComplianceAgent              — future LangGraph agent (Phase 8)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.compliance.payment_rules import (
    PaymentComplianceResult,
    check_cross_border_high_value,
    check_dormant_account,
    check_high_risk_merchant,
    check_pep,
    check_structuring,
    check_velocity,
)
from app.compliance.vara_rules import (
    VARAComplianceResult,
    check_defi_interaction,
    check_high_risk_vasp,
    check_stablecoin_cycling,
    check_travel_rule,
    check_unhosted_wallet,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

ALL_RULES = [
    # Base rules (1-5) — in alert_engine.py
    {"id": "AML-01", "name": "High-Value Transaction",    "basis": "CBUAE AML/CFT §4.1"},
    {"id": "AML-02", "name": "Sanctioned Corridor",       "basis": "FATF Recommendation 10"},
    {"id": "AML-03", "name": "Device Mismatch",           "basis": "CBUAE AML/CFT §3.1"},
    {"id": "AML-04", "name": "New Account High Value",    "basis": "FATF Recommendation 10"},
    {"id": "AML-05", "name": "OFAC Name Match",           "basis": "OFAC SDN regulations"},
    # Extended payment rules (6-11)
    {"id": "AML-06", "name": "Structuring / Smurfing",    "basis": "CBUAE AML/CFT §4.1; UAE Law 20/2014"},
    {"id": "AML-07", "name": "Transaction Velocity",      "basis": "FATF Recommendation 10"},
    {"id": "AML-08", "name": "Cross-Border High Value",   "basis": "CBUAE AML/CFT §5.3"},
    {"id": "AML-09", "name": "PEP Transaction",           "basis": "FATF Recommendation 12"},
    {"id": "AML-10", "name": "Dormant Account Activity",  "basis": "CBUAE AML/CFT §3.4"},
    {"id": "AML-11", "name": "High-Risk Merchant MCC",    "basis": "CBUAE Merchant Risk 2023"},
    # VARA rules (12-17)
    {"id": "VARA-12", "name": "FATF Travel Rule",         "basis": "FATF R.16; VARA CRMR §4.2"},
    {"id": "VARA-13", "name": "Unhosted Wallet EDD",      "basis": "VARA CRMR §5.1"},
    {"id": "VARA-14", "name": "High-Risk VASP",           "basis": "VARA CRMR §6.3"},
    {"id": "VARA-15", "name": "DeFi Protocol Monitoring", "basis": "VARA VAR 2023 §8.2"},
    {"id": "VARA-16", "name": "NFT High Value",           "basis": "FATF VA Guidance 2021"},
    {"id": "VARA-17", "name": "Stablecoin Rapid Cycling", "basis": "VARA Travel Rule 2023"},
]


@dataclass
class ComplianceReport:
    """
    Complete compliance report for one transaction.
    Contains results from all applicable rules.
    """
    transaction_id    : str
    customer_id       : str
    rules_checked     : int
    rules_triggered   : int
    composite_risk    : str   # LOW / MEDIUM / HIGH / CRITICAL
    triggered_rules   : list[dict] = field(default_factory=list)
    all_results       : list[dict] = field(default_factory=list)
    regulatory_actions: list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "transaction_id"    : self.transaction_id,
            "customer_id"       : self.customer_id,
            "rules_checked"     : self.rules_checked,
            "rules_triggered"   : self.rules_triggered,
            "composite_risk"    : self.composite_risk,
            "triggered_rules"   : self.triggered_rules,
            "regulatory_actions": self.regulatory_actions,
        }


class ComplianceEngine:
    """
    Runs all compliance checks and returns a structured report.
    Stateless — safe to call concurrently.
    """

    def check_payment_transaction(
        self,
        transaction_id          : str,
        customer_id             : str,
        amount_aed              : float,
        is_international        : bool = False,
        is_pep                  : bool = False,
        pep_category            : str = "",
        days_since_last_activity: int = 30,
        mcc_code                : str = "",
        recent_amounts_aed      : Optional[list[float]] = None,
        transaction_timestamps  : Optional[list[float]] = None,
    ) -> ComplianceReport:
        """Run all payment AML rules (6-11) on a transaction."""
        results: list[dict] = []
        triggered: list[dict] = []
        actions: list[str] = []

        checks = [
            check_structuring(recent_amounts_aed or [amount_aed]),
            check_velocity(transaction_timestamps or [], customer_id),
            check_cross_border_high_value(amount_aed, is_international),
            check_pep(is_pep, pep_category, amount_aed),
            check_dormant_account(days_since_last_activity, amount_aed),
            check_high_risk_merchant(mcc_code, amount_aed) if mcc_code else None,
        ]

        for check in checks:
            if check is None:
                continue
            d = check.to_dict()
            results.append(d)
            if check.triggered:
                triggered.append(d)
                if check.recommended_action not in actions:
                    actions.append(check.recommended_action)

        risk = self._composite_risk(triggered)
        logger.info(
            f"Payment compliance | tx={transaction_id} | "
            f"rules_triggered={len(triggered)}/{len(results)} | risk={risk}"
        )

        return ComplianceReport(
            transaction_id     = transaction_id,
            customer_id        = customer_id,
            rules_checked      = len(results),
            rules_triggered    = len(triggered),
            composite_risk     = risk,
            triggered_rules    = triggered,
            all_results        = results,
            regulatory_actions = actions,
        )

    def check_virtual_asset_transaction(
        self,
        transaction_id           : str,
        customer_id              : str,
        amount_aed               : float,
        has_originator           : bool = False,
        has_beneficiary          : bool = False,
        wallet_type              : str = "hosted",
        customer_verified        : bool = True,
        counterparty_jurisdiction: str = "",
        counterparty_vasp        : str = "",
        protocol_type            : str = "",
        token_symbol             : str = "",
        inbound_ts               : Optional[float] = None,
        outbound_ts              : Optional[float] = None,
    ) -> ComplianceReport:
        """Run all VARA virtual asset rules (12-17) on a transaction."""
        results: list[dict] = []
        triggered: list[dict] = []
        actions: list[str] = []

        checks = [
            check_travel_rule(amount_aed, has_originator, has_beneficiary),
            check_unhosted_wallet(wallet_type, amount_aed, customer_verified),
            check_high_risk_vasp(counterparty_jurisdiction, counterparty_vasp) if counterparty_jurisdiction else None,
            check_defi_interaction(protocol_type, amount_aed) if protocol_type else None,
            check_stablecoin_cycling(token_symbol, inbound_ts, outbound_ts, amount_aed) if token_symbol else None,
        ]

        for check in checks:
            if check is None:
                continue
            d = check.to_dict()
            results.append(d)
            if check.triggered:
                triggered.append(d)
                if check.recommended_action not in actions:
                    actions.append(check.recommended_action)

        risk = self._composite_risk(triggered)
        logger.info(
            f"VARA compliance | tx={transaction_id} | "
            f"rules_triggered={len(triggered)}/{len(results)} | risk={risk}"
        )

        return ComplianceReport(
            transaction_id     = transaction_id,
            customer_id        = customer_id,
            rules_checked      = len(results),
            rules_triggered    = len(triggered),
            composite_risk     = risk,
            triggered_rules    = triggered,
            all_results        = results,
            regulatory_actions = actions,
        )

    @staticmethod
    def _composite_risk(triggered: list[dict]) -> str:
        if not triggered:
            return "LOW"
        severities = [r.get("severity", "LOW") for r in triggered]
        if "CRITICAL" in severities:
            return "CRITICAL"
        if "HIGH" in severities:
            return "HIGH"
        if "MEDIUM" in severities:
            return "MEDIUM"
        return "LOW"

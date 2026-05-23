"""
app/compliance/payment_rules.py
────────────────────────────────
Extended payment AML rules (Rules 6-11).

These extend the base alert engine's 5 rules with more sophisticated
pattern detection grounded in CBUAE AML/CFT guidelines and FATF
Recommendations. Each rule cites the specific regulatory document
and article it implements.

Rules:
  Rule 6:  Structuring (smurfing) — transactions just below AED 40k threshold
  Rule 7:  Velocity — rapid transaction pattern
  Rule 8:  Cross-border wire above second threshold (AED 100k)
  Rule 9:  PEP (Politically Exposed Person) transaction
  Rule 10: Dormant account sudden large activity
  Rule 11: High-risk merchant category code
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Structuring threshold — transactions just below CBUAE reporting threshold
STRUCTURING_THRESHOLD_AED = 40_000.0
STRUCTURING_BUFFER_AED    = 5_000.0   # within AED 5k below threshold = suspicious
STRUCTURING_MIN_COUNT     = 3          # minimum 3 transactions to flag structuring

# Velocity — rapid transaction window
VELOCITY_WINDOW_SECONDS = 3_600        # 1 hour
VELOCITY_MIN_COUNT      = 5            # 5+ transactions in 1 hour

# Cross-border second threshold
CROSS_BORDER_THRESHOLD_AED = 100_000.0

# Dormant account — 6 months inactivity
DORMANT_DAYS = 180

# High-risk MCC codes — CBUAE merchant risk classification
HIGH_RISK_MCC_CODES = {
    "7995": "Gambling/Betting",
    "6211": "Securities Broker",
    "6051": "Non-Financial Institution (Crypto Exchange)",
    "7012": "Timeshares",
    "5933": "Pawnshops",
    "4829": "Money Transfer/Wire",
    "6099": "Financial Institution (Non-Bank)",
    "7801": "Online Gaming",
    "5912": "Drugstores/Pharmacies",  # high cash risk
}


@dataclass
class PaymentComplianceResult:
    """Result of one extended payment AML rule check."""
    rule_id          : str
    rule_name        : str
    triggered        : bool
    severity         : str
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


def check_structuring(
    recent_amounts_aed: list[float],
    window_hours      : int = 24,
) -> PaymentComplianceResult:
    """
    Rule 6: Structuring / Smurfing Detection

    Multiple transactions just below the AED 40,000 CBUAE reporting
    threshold within 24 hours. Structuring is a deliberate strategy
    to avoid triggering mandatory reporting — it is itself a criminal
    offence under UAE Federal Law No. 20 of 2014.

    Regulatory basis: CBUAE AML/CFT Guidelines 2023 §4.1;
    UAE Federal Law No. 20 of 2014 on AML/CFT Article 2(1)d
    """
    suspicious = [
        amt for amt in recent_amounts_aed
        if (STRUCTURING_THRESHOLD_AED - STRUCTURING_BUFFER_AED) <= amt < STRUCTURING_THRESHOLD_AED
    ]
    triggered = len(suspicious) >= STRUCTURING_MIN_COUNT
    total_aed = sum(suspicious)

    return PaymentComplianceResult(
        rule_id    = "AML-06",
        rule_name  = "Structuring / Smurfing Detection",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "CBUAE AML/CFT Guidelines 2023 §4.1 — Structuring; "
            "UAE Federal Law No. 20 of 2014 Art. 2(1)d; "
            "FATF Recommendation 10 — Customer Due Diligence"
        ),
        description = (
            f"{len(suspicious)} transactions between "
            f"AED {STRUCTURING_THRESHOLD_AED - STRUCTURING_BUFFER_AED:,.0f} and "
            f"AED {STRUCTURING_THRESHOLD_AED:,.0f} within {window_hours}h "
            f"(total: AED {total_aed:,.2f}). "
            f"{'Structuring pattern detected — deliberate threshold evasion.' if triggered else 'Normal pattern.'}"
        ),
        recommended_action = "ESCALATE_COMPLIANCE" if triggered else "PASS",
        evidence = {
            "suspicious_amounts"   : suspicious[:10],
            "suspicious_count"     : len(suspicious),
            "total_aed"            : total_aed,
            "threshold_aed"        : STRUCTURING_THRESHOLD_AED,
            "window_hours"         : window_hours,
        },
    )


def check_velocity(
    transaction_timestamps: list[float],
    customer_id           : str = "",
) -> PaymentComplianceResult:
    """
    Rule 7: Transaction Velocity

    Five or more transactions from the same customer within one hour.
    High velocity is a key indicator of account takeover, automated
    fraud, or layering — moving funds through multiple transactions
    to obscure the origin.

    Regulatory basis: FATF Recommendation 10; CBUAE AML/CFT §3.2
    """
    now = datetime.now(timezone.utc).timestamp()
    recent = [ts for ts in transaction_timestamps if now - ts <= VELOCITY_WINDOW_SECONDS]
    triggered = len(recent) >= VELOCITY_MIN_COUNT

    return PaymentComplianceResult(
        rule_id    = "AML-07",
        rule_name  = "Transaction Velocity Alert",
        triggered  = triggered,
        severity   = "MEDIUM" if triggered else "LOW",
        regulatory_basis = (
            "FATF Recommendation 10 — Customer Due Diligence; "
            "CBUAE AML/CFT Guidelines 2023 §3.2 — Unusual Transaction Patterns"
        ),
        description = (
            f"Customer {customer_id or 'unknown'}: "
            f"{len(recent)} transactions within the last hour "
            f"({'exceeds' if triggered else 'below'} threshold of {VELOCITY_MIN_COUNT})."
        ),
        recommended_action = "INVESTIGATE" if triggered else "PASS",
        evidence = {
            "recent_count"    : len(recent),
            "threshold"       : VELOCITY_MIN_COUNT,
            "window_seconds"  : VELOCITY_WINDOW_SECONDS,
            "customer_id"     : customer_id,
        },
    )


def check_cross_border_high_value(
    amount_aed : float,
    is_international: bool = True,
) -> PaymentComplianceResult:
    """
    Rule 8: Cross-Border Wire Above Second Threshold

    International wire transfers above AED 100,000 trigger enhanced
    reporting requirements under CBUAE guidelines — separate from the
    standard AED 40,000 cash transaction reporting threshold.

    Regulatory basis: CBUAE AML/CFT Guidelines 2023 §5.3
    """
    triggered = is_international and amount_aed >= CROSS_BORDER_THRESHOLD_AED

    return PaymentComplianceResult(
        rule_id    = "AML-08",
        rule_name  = "Cross-Border High-Value Wire",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "CBUAE AML/CFT Guidelines 2023 §5.3 — Cross-Border Wire Transfers; "
            "FATF Recommendation 16 — Wire Transfer Rule"
        ),
        description = (
            f"{'International' if is_international else 'Domestic'} wire of "
            f"AED {amount_aed:,.2f} — "
            f"{'exceeds' if triggered else 'below'} "
            f"cross-border enhanced reporting threshold (AED {CROSS_BORDER_THRESHOLD_AED:,.0f})."
        ),
        recommended_action = "ENHANCED_DUE_DILIGENCE" if triggered else "PASS",
        evidence = {
            "amount_aed"      : amount_aed,
            "threshold_aed"   : CROSS_BORDER_THRESHOLD_AED,
            "is_international": is_international,
        },
    )


def check_pep(
    is_pep        : bool,
    pep_category  : str = "",
    amount_aed    : float = 0.0,
) -> PaymentComplianceResult:
    """
    Rule 9: Politically Exposed Person (PEP) Transaction

    FATF Recommendation 12 requires enhanced due diligence for all
    transactions involving PEPs — senior political figures, their
    family members, and close associates. PEP status alone does not
    mean fraud, but all PEP transactions require enhanced monitoring.

    In the UAE context, PEPs include government officials, military
    officers above colonel rank, senior judiciary, and SOE directors.

    Regulatory basis: FATF Recommendation 12; CBUAE AML/CFT §6.1
    """
    triggered = is_pep

    return PaymentComplianceResult(
        rule_id    = "AML-09",
        rule_name  = "Politically Exposed Person (PEP)",
        triggered  = triggered,
        severity   = "HIGH" if triggered else "LOW",
        regulatory_basis = (
            "FATF Recommendation 12 — Politically Exposed Persons; "
            "CBUAE AML/CFT Guidelines 2023 §6.1 — PEP Enhanced Due Diligence; "
            "UAE Cabinet Decision No. 10 of 2019 on AML/CFT"
        ),
        description = (
            f"Customer is {'a PEP' if is_pep else 'not a PEP'}"
            f"{' (' + pep_category + ')' if pep_category else ''}. "
            f"Transaction amount: AED {amount_aed:,.2f}. "
            f"{'Enhanced due diligence mandatory.' if triggered else 'Standard monitoring applies.'}"
        ),
        recommended_action = "ENHANCED_DUE_DILIGENCE" if triggered else "PASS",
        evidence = {
            "is_pep"      : is_pep,
            "pep_category": pep_category,
            "amount_aed"  : amount_aed,
        },
    )


def check_dormant_account(
    days_since_last_activity: int,
    amount_aed              : float,
    min_amount_aed          : float = 5_000.0,
) -> PaymentComplianceResult:
    """
    Rule 10: Dormant Account Sudden Activity

    An account inactive for 6+ months that suddenly processes a
    large transaction is a key money laundering indicator — dormant
    accounts are sometimes purchased or taken over specifically for
    layering purposes.

    Regulatory basis: CBUAE AML/CFT Guidelines 2023 §3.4
    """
    is_dormant = days_since_last_activity >= DORMANT_DAYS
    triggered  = is_dormant and amount_aed >= min_amount_aed

    return PaymentComplianceResult(
        rule_id    = "AML-10",
        rule_name  = "Dormant Account Sudden Activity",
        triggered  = triggered,
        severity   = "MEDIUM" if triggered else "LOW",
        regulatory_basis = (
            "CBUAE AML/CFT Guidelines 2023 §3.4 — Dormant Account Monitoring; "
            "FATF Recommendation 10 — Ongoing Due Diligence"
        ),
        description = (
            f"Account inactive for {days_since_last_activity} days "
            f"({'dormant' if is_dormant else 'active'}). "
            f"Transaction amount: AED {amount_aed:,.2f}. "
            f"{'Sudden large activity on dormant account — enhanced review required.' if triggered else 'Normal.'}"
        ),
        recommended_action = "INVESTIGATE" if triggered else "PASS",
        evidence = {
            "days_inactive" : days_since_last_activity,
            "dormant_threshold": DORMANT_DAYS,
            "amount_aed"    : amount_aed,
            "is_dormant"    : is_dormant,
        },
    )


def check_high_risk_merchant(
    mcc_code: str,
    amount_aed: float = 0.0,
) -> PaymentComplianceResult:
    """
    Rule 11: High-Risk Merchant Category Code

    CBUAE classifies certain merchant categories as inherently
    high-risk for AML purposes — gambling, money transfer, crypto
    exchanges, pawnshops. Transactions at these merchants receive
    enhanced scrutiny regardless of amount.

    Regulatory basis: CBUAE Merchant Risk Classification 2023
    """
    merchant_name = HIGH_RISK_MCC_CODES.get(mcc_code)
    triggered     = merchant_name is not None

    return PaymentComplianceResult(
        rule_id    = "AML-11",
        rule_name  = "High-Risk Merchant Category",
        triggered  = triggered,
        severity   = "MEDIUM" if triggered else "LOW",
        regulatory_basis = (
            "CBUAE Merchant Risk Classification Framework 2023; "
            "CBUAE AML/CFT Guidelines 2023 §4.3 — Merchant Risk; "
            "FATF Recommendation 10 — Business Relationship Risk"
        ),
        description = (
            f"MCC {mcc_code}: {merchant_name or 'Standard merchant'}. "
            f"Amount: AED {amount_aed:,.2f}. "
            f"{'High-risk merchant category — enhanced monitoring required.' if triggered else 'Standard merchant.'}"
        ),
        recommended_action = "MONITOR" if triggered else "PASS",
        evidence = {
            "mcc_code"     : mcc_code,
            "merchant_name": merchant_name,
            "amount_aed"   : amount_aed,
            "high_risk_mcc_list": list(HIGH_RISK_MCC_CODES.keys()),
        },
    )

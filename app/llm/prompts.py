"""
app/llm/prompts.py
───────────────────
LLM prompt templates for the triage agent.

Kept separate from business logic so prompts can be iterated
without touching service code. All prompts require structured
JSON output — no free-text parsing anywhere in the pipeline.

Why JSON output?
  Machine-readable results that Pydantic can validate, store,
  and display in the dashboard without post-processing.
  This is the production pattern at Stripe and Adyen.
"""

from __future__ import annotations

from app.shared.models import Alert, KYCProfile, Transaction


TRIAGE_SYSTEM_PROMPT = """You are a senior fraud analyst at a UAE-based digital payments company.
You specialise in AML compliance under UAE Central Bank (CBUAE) regulations and FATF guidelines.

Assess the fraud alert and respond with ONLY a valid JSON object — no markdown, no preamble.

Required schema:
{
  "severity_score": <integer 0-100>,
  "severity_band": <"LOW"|"MEDIUM"|"HIGH"|"CRITICAL">,
  "initial_suspicion": <string, 1-2 sentences, the core concern>,
  "risk_factors": [<string>, ...],
  "recommended_action": <"AUTO_CLOSE"|"INVESTIGATE"|"ESCALATE_IMMEDIATELY">,
  "regulatory_flags": [<string>, ...],
  "confidence": <"LOW"|"MEDIUM"|"HIGH">
}

Scoring bands:
  0–29   LOW      → likely false positive, safe to auto-close
  30–69  MEDIUM   → warrants investigation, queue for analyst
  70–89  HIGH     → strong indicators, prioritise investigation
  90–100 CRITICAL → escalate immediately to senior analyst

Action mapping:
  AUTO_CLOSE           → score < 30
  INVESTIGATE          → score 30–89
  ESCALATE_IMMEDIATELY → score ≥ 90

UAE regulatory context:
  - CBUAE threshold: AED 40,000 for cash transaction reporting
  - FATF high-risk jurisdictions: IR, KP, SY, MM, YE, SD, PK, NG, HT
  - New accounts (<30 days) + large transactions = high-risk FATF pattern
  - Device mismatch = possible account takeover

Be concise, evidence-based. Do not invent information not present in the input."""


def build_triage_prompt(
    alert: Alert,
    transaction: Transaction | None = None,
    kyc_profile: KYCProfile | None = None,
) -> str:
    """
    Build the user-facing triage prompt for a specific alert.

    Injects all available context into a structured template.
    Missing data is explicitly noted — the LLM never hallucinates missing fields.
    """
    lines = [
        "=== FRAUD ALERT TRIAGE REQUEST ===",
        "",
        f"Alert ID    : {alert.alert_id}",
        f"Trigger     : {alert.trigger.value}",
        f"Customer ID : {alert.customer_id}",
        f"Created     : {alert.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    # ── Transaction context ───────────────────────────────────────────────────
    if transaction:
        lines += [
            "--- TRANSACTION ---",
            f"TX ID     : {transaction.tx_id}",
            f"Amount    : AED {float(transaction.amount_aed):,.2f}",
            f"Merchant  : {transaction.merchant}",
            f"Country   : {transaction.country}",
            f"Timestamp : {transaction.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            f"Hour (UTC): {transaction.timestamp.hour:02d}:00",
            "",
        ]
        if float(transaction.amount_aed) > 40_000:
            lines.append(
                "⚠ REGULATORY: Amount exceeds AED 40,000 CBUAE reporting threshold"
            )
            lines.append("")
    else:
        lines += ["--- TRANSACTION ---", "Not available", ""]

    # ── KYC context ───────────────────────────────────────────────────────────
    if kyc_profile:
        lines += [
            "--- KYC PROFILE ---",
            f"Nationality      : {kyc_profile.nationality}",
            f"Account age      : {kyc_profile.account_age_days} days",
            f"Device mismatch  : {kyc_profile.has_device_mismatch}",
            f"New account (<30d): {kyc_profile.is_new_account}",
            f"Risk tier        : {kyc_profile.risk_tier.value}",
            "",
        ]
        if kyc_profile.has_device_mismatch:
            lines.append("⚠ RISK SIGNAL: Device fingerprint mismatch (possible account takeover)")
            lines.append("")
        if kyc_profile.is_new_account:
            lines.append("⚠ RISK SIGNAL: Account < 30 days — FATF new account pattern")
            lines.append("")
    else:
        lines += ["--- KYC PROFILE ---", "Not available", ""]

    lines += [
        "=== END OF ALERT DATA ===",
        "",
        "Produce your triage assessment as a JSON object following the schema in your instructions.",
    ]

    return "\n".join(lines)

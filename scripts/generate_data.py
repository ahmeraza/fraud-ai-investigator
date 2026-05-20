"""
scripts/generate_data.py
─────────────────────────
Generates three synthetic JSON datasets for the fraud investigation system:

  1. app/data/transactions.json     — 50 financial transactions (20% flagged)
  2. app/data/kyc_profiles.json     — 10 KYC customer profiles
  3. app/data/sanctions_watchlist.json — Fictional high-risk entities

Data is MENA-focused:
  - Amounts in AED (UAE Dirham)
  - Country mix reflects UAE/GCC + FATF high-risk corridors
  - All names and companies are synthetic — no real personal data

Run with:
    uv run python scripts/generate_data.py
"""

from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from faker import Faker

# ── Constants ─────────────────────────────────────────────────────────────────

RANDOM_SEED = 42
NUM_TRANSACTIONS = 50
NUM_CUSTOMERS = 10
FRAUD_RATE = 0.20  # 20% of transactions are suspicious

# UAE Central Bank reporting threshold
HIGH_VALUE_THRESHOLD_AED = 40_000

# Low-risk GCC/Western countries for normal transactions
LOW_RISK_COUNTRIES = ["AE", "SA", "AE", "AE", "GB", "AE", "US", "AE", "IN", "AE"]

# High-risk corridors (FATF-listed + UN sanctioned)
HIGH_RISK_COUNTRIES = ["IR", "KP", "SY", "MM", "YE", "SD"]

# ── Setup ─────────────────────────────────────────────────────────────────────

fake = Faker()
random.seed(RANDOM_SEED)
Faker.seed(RANDOM_SEED)

OUTPUT_DIR = Path(__file__).parent.parent / "app" / "data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Generators ────────────────────────────────────────────────────────────────


def generate_transactions(
    num: int = NUM_TRANSACTIONS,
    num_customers: int = NUM_CUSTOMERS,
) -> list[dict]:
    """
    Generate synthetic financial transactions.

    Suspicious transactions have:
      - Higher amounts (often above AED 40k reporting threshold)
      - High-risk country corridors (IR, KP, SY, etc.)
      - Unusual hours (2–5am)
    """
    transactions = []

    for i in range(num):
        is_suspicious = random.random() < FRAUD_RATE
        customer_id = f"CUST{random.randint(1, num_customers):03d}"

        # Amount: suspicious ones are large or just above threshold
        if is_suspicious:
            amount = round(random.uniform(HIGH_VALUE_THRESHOLD_AED + 1, 500_000), 2)
        else:
            amount = round(random.uniform(50, HIGH_VALUE_THRESHOLD_AED - 1), 2)

        # Country: suspicious ones use high-risk corridors
        country = (
            random.choice(HIGH_RISK_COUNTRIES)
            if is_suspicious
            else random.choice(LOW_RISK_COUNTRIES)
        )

        # Timestamp: suspicious ones favour odd hours
        if is_suspicious:
            hours_back = random.randint(0, 168)  # within last week
            tx_time = datetime.utcnow() - timedelta(
                hours=hours_back,
                minutes=random.randint(0, 59),
            )
            # Skew toward 2–5am for suspicious transactions
            tx_time = tx_time.replace(hour=random.randint(2, 5))
        else:
            tx_time = datetime.utcnow() - timedelta(
                hours=random.randint(0, 720),
                minutes=random.randint(0, 59),
            )

        transactions.append({
            "tx_id": str(uuid.uuid4()),
            "customer_id": customer_id,
            "amount_aed": amount,
            "currency": "AED",
            "merchant": fake.company(),
            "country": country,
            "timestamp": tx_time.isoformat() + "Z",
            "is_flagged": is_suspicious,
            "_note": "Ground truth label for evaluation only — not available in production",
        })

    return transactions


def generate_kyc_profiles(num: int = NUM_CUSTOMERS) -> list[dict]:
    """
    Generate KYC (Know Your Customer) profiles.

    Device mismatch (device_id != last_known_device) is a fraud signal.
    Customers with risk_tier HIGH are pre-flagged in the onboarding system.
    """
    profiles = []

    for i in range(1, num + 1):
        device_id = str(uuid.uuid4())

        # 30% chance of device mismatch (suspicious)
        has_mismatch = random.random() < 0.30
        last_device = str(uuid.uuid4()) if has_mismatch else device_id

        # Account age — some accounts are very new (risk signal)
        account_age = random.choice(
            [random.randint(1, 25)] * 2 +   # new accounts (riskier)
            [random.randint(30, 2000)] * 8   # established accounts
        )

        profiles.append({
            "customer_id": f"CUST{i:03d}",
            "name": fake.name(),
            "nationality": random.choice(["AE", "IN", "PK", "GB", "US", "EG", "PH", "LB"]),
            "account_age_days": account_age,
            "device_id": device_id,
            "last_known_device": last_device,
            "has_device_mismatch": has_mismatch,
            "risk_tier": random.choice(["LOW", "LOW", "LOW", "MEDIUM", "HIGH"]),
            "email_verified": random.choice([True, True, True, False]),
            "phone_verified": random.choice([True, True, True, False]),
        })

    return profiles


def generate_sanctions_watchlist() -> list[dict]:
    """
    Generate a fictional sanctions watchlist.

    All names are synthetic — no real entities.
    Includes Arabic name variants to test transliteration matching.
    """
    return [
        {
            "name": "Shell Holdings International Ltd",
            "aliases": ["Shell Holdings Intl", "SHI Ltd"],
            "country": "IR",
            "reason": "OFAC SDN — sanctions evasion",
            "date_listed": "2022-03-15",
        },
        {
            "name": "Eastern Star Trading Corp",
            "aliases": ["East Star Trading", "ESTC"],
            "country": "KP",
            "reason": "UN Security Council — weapons proliferation",
            "date_listed": "2021-08-10",
        },
        {
            "name": "Gulf Resources FZE",
            "aliases": ["Gulf Resources Free Zone", "GR-FZE"],
            "country": "SY",
            "reason": "EU Council — conflict financing",
            "date_listed": "2023-01-22",
        },
        {
            "name": "Mohamed Al Rashid Trading",
            "aliases": [
                "Mohammed Al Rashid Trading",
                "Muhammad Al Rashid Trading",
                "M. Rashid Trading LLC",
            ],
            "country": "YE",
            "reason": "OFAC — terrorism financing",
            "date_listed": "2023-06-05",
        },
        {
            "name": "Horizon Capital Group",
            "aliases": ["Horizon Capital", "HCG Holdings"],
            "country": "MM",
            "reason": "EU — military financing",
            "date_listed": "2022-11-30",
        },
    ]


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 55)
    print("  Fraud AI Investigator — Synthetic Data Generator")
    print("=" * 55)

    # Transactions
    print("\n[1/3] Generating transactions...")
    transactions = generate_transactions()
    tx_path = OUTPUT_DIR / "transactions.json"
    with open(tx_path, "w") as f:
        json.dump(transactions, f, indent=2, default=str)
    flagged = sum(1 for t in transactions if t["is_flagged"])
    print(f"      ✓ {len(transactions)} transactions ({flagged} flagged) → {tx_path}")

    # KYC profiles
    print("\n[2/3] Generating KYC profiles...")
    profiles = generate_kyc_profiles()
    kyc_path = OUTPUT_DIR / "kyc_profiles.json"
    with open(kyc_path, "w") as f:
        json.dump(profiles, f, indent=2)
    mismatches = sum(1 for p in profiles if p["has_device_mismatch"])
    print(f"      ✓ {len(profiles)} profiles ({mismatches} device mismatches) → {kyc_path}")

    # Sanctions watchlist
    print("\n[3/3] Generating sanctions watchlist...")
    sanctions = generate_sanctions_watchlist()
    sanctions_path = OUTPUT_DIR / "sanctions_watchlist.json"
    with open(sanctions_path, "w") as f:
        json.dump(sanctions, f, indent=2)
    print(f"      ✓ {len(sanctions)} entities → {sanctions_path}")

    print("\n" + "=" * 55)
    print("  All data files generated successfully!")
    print("  Next: uv run uvicorn app.main:app --reload")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()

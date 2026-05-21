"""
scripts/load_ieee_data.py
──────────────────────────
Processes the IEEE-CIS Fraud Detection dataset into the format
used by the Fraud AI Investigator pipeline.

What is the IEEE-CIS dataset?
  A real-world e-commerce transaction dataset from Vesta Corporation,
  released for the 2019 Kaggle IEEE-CIS Fraud Detection competition.
  590,540 transactions with 434 features and ground-truth fraud labels.
  This is the gold standard benchmark dataset for payment fraud detection.

Why use it?
  Your synthetic Faker data has perfectly clean distributions by design.
  IEEE-CIS has real-world characteristics: missing values, skewed amounts,
  imbalanced classes (3.5% fraud rate), temporal patterns, and device signals.
  Precision/recall numbers against real data are credible in interviews.
  Against synthetic data they are illustrative only.

How to get the data (free, Option B — manual download):
  1. Go to: https://www.kaggle.com/competitions/ieee-fraud-detection/data
  2. Create a free Kaggle account if you don't have one
  3. Accept the competition rules (click "I Understand and Accept")
  4. Download: train_transaction.csv and train_identity.csv
  5. Place both files in: app/data/ieee_cis/
  6. Run: uv run python scripts/load_ieee_data.py

File sizes:
  train_transaction.csv  ~500MB
  train_identity.csv     ~34MB

Output files (much smaller — processed samples):
  app/data/ieee_cis/ieee_transactions.json    → 2,000 sampled transactions
  app/data/ieee_cis/ieee_metadata.json        → dataset statistics
  app/data/ieee_cis/ieee_feature_stats.json   → feature distributions

Flags:
  --sample N    Number of transactions to sample (default: 2000)
  --seed N      Random seed for reproducibility (default: 42)
  --verify      Just verify files exist and show stats, no processing

Run:
  uv run python scripts/load_ieee_data.py
  uv run python scripts/load_ieee_data.py --sample 5000
  uv run python scripts/load_ieee_data.py --verify
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR     = PROJECT_ROOT / "app" / "data" / "ieee_cis"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TX_CSV_PATH       = DATA_DIR / "train_transaction.csv"
IDENTITY_CSV_PATH = DATA_DIR / "train_identity.csv"
OUTPUT_TX_PATH    = DATA_DIR / "ieee_transactions.json"
OUTPUT_META_PATH  = DATA_DIR / "ieee_metadata.json"
OUTPUT_STATS_PATH = DATA_DIR / "ieee_feature_stats.json"

# ── Key IEEE-CIS features we use ─────────────────────────────────────────────
# The full dataset has 434 features. We select the ones most relevant
# to our alert engine rules. Full list at:
# https://www.kaggle.com/competitions/ieee-fraud-detection/data

TRANSACTION_FEATURES = [
    "TransactionID",
    "isFraud",           # ground truth label (1 = fraud, 0 = legitimate)
    "TransactionDT",     # seconds offset from a reference datetime
    "TransactionAmt",    # transaction amount in USD
    "ProductCD",         # product code (W, H, C, S, R)
    "card1",             # payment card information
    "card4",             # card network (visa, mastercard, etc.)
    "card6",             # card type (debit, credit)
    "P_emaildomain",     # purchaser email domain
    "R_emaildomain",     # recipient email domain
    "addr1",             # billing address zip code
    "addr2",             # billing address country code
    "dist1",             # distance between billing/shipping addresses
    "C1", "C2",          # counting features (how many addresses per card, etc.)
    "D1",                # days since card was first used
    "M1", "M2", "M3",    # match features (name, address, zip match)
    "V1", "V3", "V4",    # Vesta engineered features
]

IDENTITY_FEATURES = [
    "TransactionID",
    "DeviceType",        # desktop / mobile
    "DeviceInfo",        # device details
    "id_12",             # "Found" / "Not Found"
    "id_15",             # browser / OS match
    "id_16",             # unknown
    "id_28",             # "New" / "Found" (new device)
    "id_29",             # "New" / "Found"
    "id_31",             # browser
    "id_35",             # True/False
    "id_36",             # True/False
    "id_37",             # True/False
    "id_38",             # True/False
]

# Reference timestamp — TransactionDT is seconds from this point
# Determined by community analysis of the competition data
REFERENCE_DT = datetime(2017, 11, 30, tzinfo=timezone.utc)


def verify_files() -> bool:
    """Check that the required CSV files exist and are readable."""
    print("Verifying IEEE-CIS data files...")
    print(f"  Looking in: {DATA_DIR}")
    print()

    tx_ok  = TX_CSV_PATH.exists()
    id_ok  = IDENTITY_CSV_PATH.exists()

    print(f"  {'✓' if tx_ok else '✗'} train_transaction.csv"
          + (f"  ({TX_CSV_PATH.stat().st_size / 1024 / 1024:.0f}MB)" if tx_ok else " — NOT FOUND"))
    print(f"  {'✓' if id_ok else '✗'} train_identity.csv"
          + (f"  ({IDENTITY_CSV_PATH.stat().st_size / 1024 / 1024:.0f}MB)" if id_ok else " — NOT FOUND"))

    if not tx_ok or not id_ok:
        print()
        print("  Missing files. Download instructions:")
        print("  1. Go to: https://www.kaggle.com/competitions/ieee-fraud-detection/data")
        print("  2. Accept competition rules")
        print("  3. Download train_transaction.csv and train_identity.csv")
        print(f"  4. Place both in: {DATA_DIR}")
        print("  5. Re-run this script")
        return False

    print()
    print("  Both files present ✓")
    return True


def load_and_merge(sample_n: int, seed: int) -> pd.DataFrame:
    """
    Load IEEE-CIS CSVs, select key features, merge on TransactionID,
    and return a sampled DataFrame with stratified fraud/non-fraud split.
    """
    print("\n[1/4] Loading transaction CSV...")
    t0 = time.time()

    # Load only the columns we need — much faster than loading all 394 tx columns
    tx_cols_available = pd.read_csv(TX_CSV_PATH, nrows=0).columns.tolist()
    tx_cols_to_load   = [c for c in TRANSACTION_FEATURES if c in tx_cols_available]

    tx_df = pd.read_csv(TX_CSV_PATH, usecols=tx_cols_to_load)
    print(f"  {len(tx_df):,} transactions loaded in {time.time()-t0:.1f}s")

    print("\n[2/4] Loading identity CSV...")
    t0 = time.time()
    id_cols_available = pd.read_csv(IDENTITY_CSV_PATH, nrows=0).columns.tolist()
    id_cols_to_load   = [c for c in IDENTITY_FEATURES if c in id_cols_available]

    id_df = pd.read_csv(IDENTITY_CSV_PATH, usecols=id_cols_to_load)
    print(f"  {len(id_df):,} identity records loaded in {time.time()-t0:.1f}s")

    print("\n[3/4] Merging and sampling...")
    merged = tx_df.merge(id_df, on="TransactionID", how="left")
    print(f"  Merged: {len(merged):,} rows | {len(merged.columns)} columns")

    fraud_rate = merged["isFraud"].mean()
    print(f"  Fraud rate: {fraud_rate:.2%} ({merged['isFraud'].sum():,} fraud cases)")

    # Stratified sample — preserve the real fraud rate
    fraud     = merged[merged["isFraud"] == 1]
    non_fraud = merged[merged["isFraud"] == 0]

    n_fraud     = min(len(fraud),     int(sample_n * fraud_rate))
    n_non_fraud = sample_n - n_fraud

    sampled = pd.concat([
        fraud.sample(n=n_fraud,     random_state=seed),
        non_fraud.sample(n=n_non_fraud, random_state=seed),
    ]).sample(frac=1, random_state=seed).reset_index(drop=True)

    print(f"  Sampled: {len(sampled):,} rows "
          f"({n_fraud} fraud, {n_non_fraud} non-fraud)")

    return sampled, merged


def convert_to_pipeline_format(df: pd.DataFrame) -> list[dict]:
    """
    Convert IEEE-CIS rows to the Transaction schema used by our pipeline.

    Mapping decisions:
      TransactionAmt (USD) → amount_aed (multiply by 3.67 AED/USD rate)
      addr2 (numeric country code) → country (mapped to ISO codes where possible)
      DeviceType → used in device mismatch signal
      D1 (days since card first used) → account_age_days proxy
      id_28 ("New") → new device signal
    """
    # Rough country code mapping from IEEE numeric addr2 codes
    # These are anonymised — we map known patterns
    COUNTRY_MAP = {
        87.0 : "US", 96.0: "US", 60.0: "GB", 65.0: "CA",
        166.0: "AU", 31.0: "DE", 36.0: "FR", 59.0: "NL",
    }
    USD_TO_AED = 3.674  # fixed rate for consistency

    records = []
    for _, row in df.iterrows():
        # Convert TransactionDT (seconds offset) to a real timestamp
        try:
            tx_dt = pd.Timestamp(REFERENCE_DT) + pd.Timedelta(seconds=float(row["TransactionDT"]))
            timestamp_str = tx_dt.isoformat()
        except Exception:
            timestamp_str = datetime.now(timezone.utc).isoformat()

        # Map country
        addr2   = row.get("addr2", None)
        country = COUNTRY_MAP.get(addr2, "US") if pd.notna(addr2) else "US"

        # Device mismatch proxy from id_28 ("New" = new device not in history)
        id_28           = str(row.get("id_28", "")).strip()
        device_is_new   = id_28.lower() == "new"

        # Account age proxy from D1 (days since card first used)
        d1              = row.get("D1", None)
        account_age_days= int(d1) if pd.notna(d1) else 365

        # Amount in AED
        amount_usd = float(row.get("TransactionAmt", 0))
        amount_aed = round(amount_usd * USD_TO_AED, 2)

        # Product code as merchant category
        product_cd = str(row.get("ProductCD", "W")).strip()
        merchant_map = {
            "W": "E-commerce",
            "H": "Hotel/Travel",
            "C": "Mobile/Telecom",
            "S": "Sports/Entertainment",
            "R": "Financial Services",
        }
        merchant_category = merchant_map.get(product_cd, "E-commerce")

        # Card network
        card4 = str(row.get("card4", "unknown")).strip()
        card6 = str(row.get("card6", "debit")).strip()

        record = {
            # Core transaction fields (matches our Transaction model)
            "tx_id"            : f"IEEE-{int(row['TransactionID'])}",
            "customer_id"      : f"CARD-{int(row.get('card1', 0))}",
            "amount_aed"       : amount_aed,
            "amount_usd"       : amount_usd,
            "currency"         : "AED",
            "merchant"         : f"{merchant_category} ({product_cd})",
            "country"          : country,
            "timestamp"        : timestamp_str,
            "is_flagged"       : bool(row["isFraud"]),

            # IEEE-CIS enrichment fields (for notebook analysis)
            "source"           : "ieee_cis",
            "product_code"     : product_cd,
            "card_network"     : card4 if pd.notna(row.get("card4")) else "unknown",
            "card_type"        : card6 if pd.notna(row.get("card6")) else "unknown",
            "device_type"      : str(row.get("DeviceType", "")).strip() or "unknown",
            "device_is_new"    : device_is_new,
            "account_age_days" : account_age_days,
            "purchaser_email"  : str(row.get("P_emaildomain", "")).strip() or "unknown",
            "dist1"            : float(row["dist1"]) if pd.notna(row.get("dist1")) else None,

            # Address match signals (M features: T/F/nan)
            "addr_match_name"  : str(row.get("M1", "")).strip() or None,
            "addr_match_street": str(row.get("M2", "")).strip() or None,
            "addr_match_zip"   : str(row.get("M3", "")).strip() or None,
        }
        records.append(record)

    return records


def compute_feature_stats(df: pd.DataFrame) -> dict:
    """Compute summary statistics on the full loaded DataFrame for the notebook."""
    fraud     = df[df["isFraud"] == 1]
    non_fraud = df[df["isFraud"] == 0]

    return {
        "total_transactions"    : len(df),
        "fraud_count"           : int(df["isFraud"].sum()),
        "non_fraud_count"       : int((df["isFraud"] == 0).sum()),
        "fraud_rate"            : round(float(df["isFraud"].mean()), 4),
        "amount_usd": {
            "overall_mean"      : round(float(df["TransactionAmt"].mean()), 2),
            "fraud_mean"        : round(float(fraud["TransactionAmt"].mean()), 2),
            "non_fraud_mean"    : round(float(non_fraud["TransactionAmt"].mean()), 2),
            "median"            : round(float(df["TransactionAmt"].median()), 2),
            "p95"               : round(float(df["TransactionAmt"].quantile(0.95)), 2),
            "max"               : round(float(df["TransactionAmt"].max()), 2),
        },
        "product_codes"         : df["ProductCD"].value_counts().to_dict() if "ProductCD" in df else {},
        "card_networks"         : df["card4"].value_counts().to_dict() if "card4" in df else {},
        "device_types"          : df["DeviceType"].value_counts().to_dict() if "DeviceType" in df else {},
        "missing_values_pct"    : {
            col: round(float(df[col].isna().mean() * 100), 1)
            for col in df.columns
            if df[col].isna().any()
        },
    }


def main(sample_n: int = 2000, seed: int = 42, verify_only: bool = False) -> None:
    print("=" * 60)
    print("  IEEE-CIS FRAUD DETECTION DATASET LOADER")
    print("  Vesta Corporation / Kaggle 2019 Competition")
    print("=" * 60)

    if not verify_files():
        sys.exit(1)

    if verify_only:
        print("Verification complete (--verify mode). No processing done.")
        return

    df, full_df = load_and_merge(sample_n=sample_n, seed=seed)

    print("\n[4/4] Converting to pipeline format...")
    records = convert_to_pipeline_format(df)
    stats   = compute_feature_stats(full_df)

    # Save outputs
    OUTPUT_TX_PATH.write_text(
        json.dumps(records, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )

    metadata = {
        "source"           : "IEEE-CIS Fraud Detection — Kaggle 2019",
        "source_url"       : "https://www.kaggle.com/competitions/ieee-fraud-detection",
        "processed_at"     : datetime.now(timezone.utc).isoformat(),
        "sample_size"      : len(records),
        "sample_seed"      : seed,
        "fraud_count"      : sum(1 for r in records if r["is_flagged"]),
        "non_fraud_count"  : sum(1 for r in records if not r["is_flagged"]),
        "fraud_rate"       : round(sum(1 for r in records if r["is_flagged"]) / len(records), 4),
        "usd_to_aed_rate"  : 3.674,
        "full_dataset_size": stats["total_transactions"],
    }
    OUTPUT_META_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    OUTPUT_STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    fraud_count = sum(1 for r in records if r["is_flagged"])
    print(f"  {len(records):,} transactions → {OUTPUT_TX_PATH.name}")
    print(f"  Fraud: {fraud_count} ({fraud_count/len(records):.1%}) | "
          f"Non-fraud: {len(records)-fraud_count}")

    print("\n" + "=" * 60)
    print("  IEEE-CIS data processed successfully!")
    print(f"  {len(records):,} transactions ready for pipeline")
    print("  Next: uv run uvicorn app.main:app --reload")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Process IEEE-CIS fraud dataset")
    p.add_argument("--sample", type=int, default=2000,
                   help="Number of transactions to sample (default: 2000)")
    p.add_argument("--seed",   type=int, default=42,
                   help="Random seed for reproducibility (default: 42)")
    p.add_argument("--verify", action="store_true",
                   help="Only verify files exist, do not process")
    args = p.parse_args()
    main(sample_n=args.sample, seed=args.seed, verify_only=args.verify)

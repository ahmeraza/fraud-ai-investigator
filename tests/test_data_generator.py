"""
tests/test_data_generator.py
──────────────────────────────
Tests for the synthetic data generator script.

Run with:
    uv run pytest tests/test_data_generator.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.generate_data import (
    generate_kyc_profiles,
    generate_sanctions_watchlist,
    generate_transactions,
)


class TestTransactionGenerator:
    def test_generates_correct_count(self):
        txs = generate_transactions(num=30)
        assert len(txs) == 30

    def test_all_have_required_fields(self):
        txs = generate_transactions(num=10)
        required = {"tx_id", "customer_id", "amount_aed", "currency", "merchant", "country", "timestamp", "is_flagged"}
        for tx in txs:
            assert required.issubset(tx.keys()), f"Missing fields in {tx}"

    def test_currency_is_aed(self):
        txs = generate_transactions(num=10)
        for tx in txs:
            assert tx["currency"] == "AED"

    def test_all_amounts_are_positive(self):
        txs = generate_transactions(num=20)
        for tx in txs:
            assert tx["amount_aed"] > 0

    def test_all_tx_ids_are_unique(self):
        txs = generate_transactions(num=50)
        ids = [tx["tx_id"] for tx in txs]
        assert len(set(ids)) == 50, "Duplicate transaction IDs found"

    def test_fraud_rate_approximately_correct(self):
        # With 200 samples and 20% fraud rate, expect 30–50 flagged
        txs = generate_transactions(num=200)
        flagged = sum(1 for tx in txs if tx["is_flagged"])
        assert 25 <= flagged <= 55, f"Fraud rate out of range: {flagged}/200"

    def test_suspicious_transactions_have_high_amounts(self):
        txs = generate_transactions(num=100)
        suspicious = [tx for tx in txs if tx["is_flagged"]]
        for tx in suspicious:
            assert tx["amount_aed"] > 40_000, (
                f"Suspicious tx {tx['tx_id']} has low amount {tx['amount_aed']}"
            )

    def test_normal_transactions_below_threshold(self):
        txs = generate_transactions(num=100)
        normal = [tx for tx in txs if not tx["is_flagged"]]
        for tx in normal:
            assert tx["amount_aed"] < 40_000

    def test_suspicious_countries_are_high_risk(self):
        high_risk = {"IR", "KP", "SY", "MM", "YE", "SD"}
        txs = generate_transactions(num=100)
        suspicious = [tx for tx in txs if tx["is_flagged"]]
        for tx in suspicious:
            assert tx["country"] in high_risk, (
                f"Suspicious tx {tx['tx_id']} has unexpected country {tx['country']}"
            )

    def test_customer_ids_are_in_range(self):
        txs = generate_transactions(num=50, num_customers=10)
        for tx in txs:
            # CUST001 to CUST010
            assert tx["customer_id"].startswith("CUST")
            num = int(tx["customer_id"][4:])
            assert 1 <= num <= 10


class TestKYCGenerator:
    def test_generates_correct_count(self):
        profiles = generate_kyc_profiles(num=5)
        assert len(profiles) == 5

    def test_customer_ids_are_sequential(self):
        profiles = generate_kyc_profiles(num=5)
        expected_ids = [f"CUST{i:03d}" for i in range(1, 6)]
        actual_ids = [p["customer_id"] for p in profiles]
        assert actual_ids == expected_ids

    def test_all_have_required_fields(self):
        profiles = generate_kyc_profiles(num=3)
        required = {"customer_id", "name", "nationality", "account_age_days",
                    "device_id", "last_known_device", "has_device_mismatch", "risk_tier"}
        for p in profiles:
            assert required.issubset(p.keys())

    def test_device_mismatch_flag_is_correct(self):
        profiles = generate_kyc_profiles(num=20)
        for p in profiles:
            expected_mismatch = p["device_id"] != p["last_known_device"]
            assert p["has_device_mismatch"] == expected_mismatch

    def test_risk_tiers_are_valid(self):
        valid_tiers = {"LOW", "MEDIUM", "HIGH"}
        profiles = generate_kyc_profiles(num=10)
        for p in profiles:
            assert p["risk_tier"] in valid_tiers

    def test_account_age_is_non_negative(self):
        profiles = generate_kyc_profiles(num=10)
        for p in profiles:
            assert p["account_age_days"] >= 0


class TestSanctionsGenerator:
    def test_returns_list(self):
        sanctions = generate_sanctions_watchlist()
        assert isinstance(sanctions, list)
        assert len(sanctions) > 0

    def test_all_have_required_fields(self):
        sanctions = generate_sanctions_watchlist()
        required = {"name", "aliases", "country", "reason", "date_listed"}
        for entry in sanctions:
            assert required.issubset(entry.keys())

    def test_aliases_are_lists(self):
        sanctions = generate_sanctions_watchlist()
        for entry in sanctions:
            assert isinstance(entry["aliases"], list)

    def test_arabic_name_has_transliteration_variants(self):
        sanctions = generate_sanctions_watchlist()
        # The Mohamed Al Rashid entry should have multiple Arabic name variants
        arabic_entry = next(
            (e for e in sanctions if "Mohamed" in e["name"]), None
        )
        assert arabic_entry is not None
        assert len(arabic_entry["aliases"]) >= 2, (
            "Arabic name entry should have at least 2 transliteration variants"
        )

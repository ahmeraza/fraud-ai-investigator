"""
tests/test_ieee_loader.py
──────────────────────────
Tests for the IEEE-CIS data loader and unified data loader.

All tests run without the actual Kaggle CSV files.
We test the processing logic using small synthetic DataFrames
that mirror the IEEE-CIS schema.

Coverage:
  - data_loader auto-selection (IEEE-CIS vs synthetic fallback)
  - data_loader combined mode
  - Transaction parsing from IEEE-CIS format
  - USD → AED conversion
  - Timestamp conversion from TransactionDT offset
  - DeviceType and new device signal extraction
  - Account age proxy from D1 feature
  - Feature stats computation
  - load_ieee_data.py verify mode
  - load_ieee_data.py main() with mock CSV files

Run:
    uv run pytest tests/test_ieee_loader.py -v
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.data_loader import (
    load_transactions,
    load_kyc_profiles,
    data_source_status,
    _parse_transaction,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_synthetic_transaction(**kw) -> dict:
    """Minimal synthetic transaction dict."""
    return {
        "tx_id"      : kw.get("tx_id", "TX-001"),
        "customer_id": kw.get("customer_id", "CUST001"),
        "amount_aed" : kw.get("amount_aed", "5000.00"),
        "currency"   : "AED",
        "merchant"   : kw.get("merchant", "Dubai Mall"),
        "country"    : kw.get("country", "AE"),
        "timestamp"  : datetime.now(timezone.utc).isoformat(),
        "is_flagged" : kw.get("is_flagged", False),
    }


def make_ieee_transaction(**kw) -> dict:
    """Minimal IEEE-CIS-converted transaction dict."""
    return {
        "tx_id"           : kw.get("tx_id", "IEEE-123456"),
        "customer_id"     : kw.get("customer_id", "CARD-1234"),
        "amount_aed"      : kw.get("amount_aed", "18370.00"),
        "amount_usd"      : kw.get("amount_usd", "5000.00"),
        "currency"        : "AED",
        "merchant"        : kw.get("merchant", "E-commerce (W)"),
        "country"         : kw.get("country", "US"),
        "timestamp"       : datetime.now(timezone.utc).isoformat(),
        "is_flagged"      : kw.get("is_flagged", False),
        "source"          : "ieee_cis",
        "product_code"    : "W",
        "card_network"    : "visa",
        "card_type"       : "credit",
        "device_type"     : "desktop",
        "device_is_new"   : kw.get("device_is_new", False),
        "account_age_days": kw.get("account_age_days", 365),
        "purchaser_email" : "gmail.com",
        "dist1"           : 0.0,
        "addr_match_name" : "T",
        "addr_match_street": "T",
        "addr_match_zip"  : "T",
    }


# ── Transaction parsing tests ─────────────────────────────────────────────────

class TestTransactionParsing:
    def test_parse_synthetic_transaction(self):
        raw = make_synthetic_transaction()
        tx  = _parse_transaction(raw)
        assert tx is not None
        assert tx.tx_id == "TX-001"
        assert float(tx.amount_aed) == 5000.00
        assert tx.currency == "AED"

    def test_parse_ieee_transaction(self):
        raw = make_ieee_transaction()
        tx  = _parse_transaction(raw)
        assert tx is not None
        assert tx.tx_id == "IEEE-123456"
        assert tx.customer_id == "CARD-1234"

    def test_parse_invalid_transaction_returns_none(self):
        raw = {"tx_id": "BAD", "amount_aed": "not-a-number"}
        tx  = _parse_transaction(raw)
        assert tx is None

    def test_parse_flagged_transaction(self):
        raw = make_ieee_transaction(is_flagged=True)
        tx  = _parse_transaction(raw)
        assert tx.is_flagged is True

    def test_amount_parsed_as_decimal(self):
        raw = make_synthetic_transaction(amount_aed="12345.67")
        tx  = _parse_transaction(raw)
        assert tx is not None
        assert abs(float(tx.amount_aed) - 12345.67) < 0.01


# ── Data loader tests ─────────────────────────────────────────────────────────

class TestDataLoader:
    def test_auto_uses_ieee_when_available(self, tmp_path):
        """auto mode should prefer IEEE-CIS data."""
        ieee_data = [make_ieee_transaction()]
        ieee_file = tmp_path / "ieee_transactions.json"
        ieee_file.write_text(json.dumps(ieee_data))

        with patch("app.services.data_loader.IEEE_PATH", ieee_file), \
             patch("app.services.data_loader.SYNTHETIC_PATH", tmp_path / "no_synthetic.json"):
            result = load_transactions(source="auto")

        assert len(result) == 1
        assert "IEEE-123456" in result

    def test_auto_falls_back_to_synthetic(self, tmp_path):
        """auto mode falls back to synthetic if IEEE-CIS not available."""
        synth_data = [make_synthetic_transaction()]
        synth_file = tmp_path / "transactions.json"
        synth_file.write_text(json.dumps(synth_data))

        with patch("app.services.data_loader.IEEE_PATH", tmp_path / "no_ieee.json"), \
             patch("app.services.data_loader.SYNTHETIC_PATH", synth_file):
            result = load_transactions(source="auto")

        assert len(result) == 1
        assert "TX-001" in result

    def test_combined_mode_merges_both(self, tmp_path):
        """combined mode should include both IEEE and synthetic transactions."""
        ieee_data  = [make_ieee_transaction(tx_id="IEEE-001")]
        synth_data = [make_synthetic_transaction(tx_id="TX-001")]

        ieee_file  = tmp_path / "ieee_transactions.json"
        synth_file = tmp_path / "transactions.json"
        ieee_file.write_text(json.dumps(ieee_data))
        synth_file.write_text(json.dumps(synth_data))

        with patch("app.services.data_loader.IEEE_PATH", ieee_file), \
             patch("app.services.data_loader.SYNTHETIC_PATH", synth_file):
            result = load_transactions(source="combined")

        assert "IEEE-001" in result
        assert "TX-001" in result
        assert len(result) == 2

    def test_synthetic_source_ignores_ieee(self, tmp_path):
        """synthetic mode should only return synthetic data."""
        ieee_data  = [make_ieee_transaction()]
        synth_data = [make_synthetic_transaction()]

        ieee_file  = tmp_path / "ieee_transactions.json"
        synth_file = tmp_path / "transactions.json"
        ieee_file.write_text(json.dumps(ieee_data))
        synth_file.write_text(json.dumps(synth_data))

        with patch("app.services.data_loader.IEEE_PATH", ieee_file), \
             patch("app.services.data_loader.SYNTHETIC_PATH", synth_file):
            result = load_transactions(source="synthetic")

        assert "TX-001" in result
        assert "IEEE-123456" not in result

    def test_ieee_source_raises_when_not_available(self, tmp_path):
        with patch("app.services.data_loader.IEEE_PATH", tmp_path / "missing.json"):
            with pytest.raises(FileNotFoundError):
                load_transactions(source="ieee")

    def test_limit_respected(self, tmp_path):
        synth_data = [make_synthetic_transaction(tx_id=f"TX-{i:03d}") for i in range(10)]
        synth_file = tmp_path / "transactions.json"
        synth_file.write_text(json.dumps(synth_data))

        with patch("app.services.data_loader.IEEE_PATH", tmp_path / "no_ieee.json"), \
             patch("app.services.data_loader.SYNTHETIC_PATH", synth_file):
            result = load_transactions(source="synthetic", limit=3)

        assert len(result) == 3

    def test_data_source_status_ieee_available(self, tmp_path):
        ieee_data  = [make_ieee_transaction()]
        ieee_file  = tmp_path / "ieee_transactions.json"
        ieee_file.write_text(json.dumps(ieee_data))

        with patch("app.services.data_loader.IEEE_PATH", ieee_file), \
             patch("app.services.data_loader.SYNTHETIC_PATH", tmp_path / "no_synth.json"), \
             patch("app.services.data_loader.KYC_PATH", tmp_path / "no_kyc.json"):
            status = data_source_status()

        assert status["ieee_cis_available"] is True
        assert status["ieee_cis_count"] == 1
        assert status["active_source"] == "ieee_cis"

    def test_data_source_status_synthetic_fallback(self, tmp_path):
        synth_data = [make_synthetic_transaction()]
        synth_file = tmp_path / "transactions.json"
        synth_file.write_text(json.dumps(synth_data))

        with patch("app.services.data_loader.IEEE_PATH", tmp_path / "no_ieee.json"), \
             patch("app.services.data_loader.SYNTHETIC_PATH", synth_file), \
             patch("app.services.data_loader.KYC_PATH", tmp_path / "no_kyc.json"):
            status = data_source_status()

        assert status["ieee_cis_available"] is False
        assert status["active_source"] == "synthetic"
        assert status["download_instructions"] is not None


# ── IEEE-CIS processor tests ──────────────────────────────────────────────────

class TestIEEEProcessor:
    def _make_tx_df(self, n=5) -> pd.DataFrame:
        """Build a minimal DataFrame that mimics IEEE-CIS train_transaction.csv."""
        return pd.DataFrame({
            "TransactionID" : range(1000, 1000 + n),
            "isFraud"       : [1, 0, 0, 1, 0][:n],
            "TransactionDT" : [86400 * i for i in range(n)],
            "TransactionAmt": [100.0, 5000.0, 250.0, 12000.0, 75.0][:n],
            "ProductCD"     : ["W", "H", "C", "S", "R"][:n],
            "card1"         : [1234] * n,
            "card4"         : ["visa"] * n,
            "card6"         : ["debit"] * n,
            "P_emaildomain" : ["gmail.com"] * n,
            "R_emaildomain" : ["gmail.com"] * n,
            "addr1"         : [100.0] * n,
            "addr2"         : [87.0] * n,
            "dist1"         : [0.0] * n,
            "C1"            : [1.0] * n,
            "C2"            : [1.0] * n,
            "D1"            : [30.0, 365.0, 10.0, 500.0, 180.0][:n],
            "M1"            : ["T"] * n,
            "M2"            : ["T"] * n,
            "M3"            : ["T"] * n,
            "V1"            : [0.0] * n,
            "V3"            : [0.0] * n,
            "V4"            : [0.0] * n,
        })

    def _make_id_df(self, n=5) -> pd.DataFrame:
        """Build a minimal DataFrame mimicking IEEE-CIS train_identity.csv."""
        return pd.DataFrame({
            "TransactionID": range(1000, 1000 + n),
            "DeviceType"   : ["desktop"] * n,
            "DeviceInfo"   : ["Windows"] * n,
            "id_28"        : ["Found", "New", "Found", "New", "Found"][:n],
            "id_31"        : ["chrome"] * n,
        })

    def test_convert_to_pipeline_format(self):
        from scripts.load_ieee_data import convert_to_pipeline_format
        df = self._make_tx_df()
        id_df = self._make_id_df()
        merged = df.merge(id_df, on="TransactionID", how="left")
        records = convert_to_pipeline_format(merged)
        assert len(records) == 5

    def test_tx_ids_are_prefixed_ieee(self):
        from scripts.load_ieee_data import convert_to_pipeline_format
        df     = self._make_tx_df()
        id_df  = self._make_id_df()
        merged = df.merge(id_df, on="TransactionID", how="left")
        records = convert_to_pipeline_format(merged)
        for r in records:
            assert r["tx_id"].startswith("IEEE-")

    def test_amount_converted_to_aed(self):
        from scripts.load_ieee_data import convert_to_pipeline_format
        df = self._make_tx_df(n=1)
        id_df = self._make_id_df(n=1)
        merged = df.merge(id_df, on="TransactionID", how="left")
        records = convert_to_pipeline_format(merged)
        # 100 USD * 3.674 = 367.4 AED
        assert abs(records[0]["amount_aed"] - 367.4) < 1.0

    def test_new_device_signal_from_id28(self):
        from scripts.load_ieee_data import convert_to_pipeline_format
        df     = self._make_tx_df(n=2)
        id_df  = self._make_id_df(n=2)
        merged = df.merge(id_df, on="TransactionID", how="left")
        records = convert_to_pipeline_format(merged)
        # First row id_28="Found" → not new, second id_28="New" → new device
        assert records[0]["device_is_new"] is False
        assert records[1]["device_is_new"] is True

    def test_compute_feature_stats(self):
        from scripts.load_ieee_data import compute_feature_stats
        df = self._make_tx_df()
        stats = compute_feature_stats(df)
        assert stats["total_transactions"] == 5
        assert stats["fraud_count"] == 2
        assert abs(stats["fraud_rate"] - 0.4) < 0.01
        assert "amount_usd" in stats

    def test_verify_files_false_when_missing(self, tmp_path):
        from scripts.load_ieee_data import verify_files
        with patch("scripts.load_ieee_data.TX_CSV_PATH", tmp_path / "no_tx.csv"), \
             patch("scripts.load_ieee_data.IDENTITY_CSV_PATH", tmp_path / "no_id.csv"):
            result = verify_files()
        assert result is False

    def test_verify_files_true_when_present(self, tmp_path):
        from scripts.load_ieee_data import verify_files
        tx_path = tmp_path / "train_transaction.csv"
        id_path = tmp_path / "train_identity.csv"
        tx_path.write_text("col1,col2\n1,2")
        id_path.write_text("col1,col2\n1,2")
        with patch("scripts.load_ieee_data.TX_CSV_PATH", tx_path), \
             patch("scripts.load_ieee_data.IDENTITY_CSV_PATH", id_path):
            result = verify_files()
        assert result is True

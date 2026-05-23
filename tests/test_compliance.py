"""
tests/test_compliance.py
─────────────────────────
Tests for the compliance engine — payment AML (Rules 6-11)
and VARA virtual asset rules (Rules 12-17).

All tests are offline — no API keys, no external calls.
Each rule is tested independently for trigger and non-trigger cases.

Run:
    uv run pytest tests/test_compliance.py -v
"""

from __future__ import annotations

import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.compliance.compliance_engine import ComplianceEngine, ALL_RULES
from app.compliance.payment_rules import (
    check_structuring, check_velocity, check_cross_border_high_value,
    check_pep, check_dormant_account, check_high_risk_merchant,
    STRUCTURING_THRESHOLD_AED, CROSS_BORDER_THRESHOLD_AED, DORMANT_DAYS,
)
from app.compliance.vara_rules import (
    check_travel_rule, check_unhosted_wallet, check_high_risk_vasp,
    check_defi_interaction, check_stablecoin_cycling,
    TRAVEL_RULE_THRESHOLD_AED, STABLECOIN_CYCLE_WINDOW_SECONDS,
)

client = TestClient(app)


# ── Rule registry ──────────────────────────────────────────────────────────

class TestRuleRegistry:
    def test_all_rules_count(self):
        assert len(ALL_RULES) == 17

    def test_each_rule_has_required_fields(self):
        for rule in ALL_RULES:
            assert "id"    in rule
            assert "name"  in rule
            assert "basis" in rule

    def test_rule_ids_are_unique(self):
        ids = [r["id"] for r in ALL_RULES]
        assert len(ids) == len(set(ids))


# ── Payment AML Rule 6: Structuring ────────────────────────────────────────

class TestStructuringRule:
    def test_triggers_on_three_below_threshold_amounts(self):
        amounts = [39_500, 39_800, 39_900]
        result  = check_structuring(amounts)
        assert result.triggered is True
        assert result.severity == "HIGH"
        assert result.rule_id  == "AML-06"

    def test_no_trigger_on_two_amounts(self):
        amounts = [39_500, 39_800]
        result  = check_structuring(amounts)
        assert result.triggered is False

    def test_no_trigger_on_normal_amounts(self):
        amounts = [10_000, 25_000, 5_000]
        result  = check_structuring(amounts)
        assert result.triggered is False

    def test_no_trigger_above_threshold(self):
        amounts = [42_000, 45_000, 50_000]
        result  = check_structuring(amounts)
        assert result.triggered is False

    def test_result_has_regulatory_basis(self):
        result = check_structuring([39_500, 39_800, 39_900])
        assert "CBUAE" in result.regulatory_basis
        assert "Federal Law" in result.regulatory_basis


# ── Payment AML Rule 7: Velocity ───────────────────────────────────────────

class TestVelocityRule:
    def test_triggers_on_five_recent_transactions(self):
        now = time.time()
        timestamps = [now - i * 60 for i in range(5)]  # 5 in last hour
        result = check_velocity(timestamps, "CUST001")
        assert result.triggered is True
        assert result.rule_id == "AML-07"

    def test_no_trigger_on_four_transactions(self):
        now = time.time()
        timestamps = [now - i * 60 for i in range(4)]
        result = check_velocity(timestamps)
        assert result.triggered is False

    def test_no_trigger_on_old_transactions(self):
        now = time.time()
        # All transactions more than 1 hour ago
        timestamps = [now - 7200 - i * 60 for i in range(10)]
        result = check_velocity(timestamps)
        assert result.triggered is False


# ── Payment AML Rule 8: Cross-Border ──────────────────────────────────────

class TestCrossBorderRule:
    def test_triggers_on_large_international_wire(self):
        result = check_cross_border_high_value(150_000, is_international=True)
        assert result.triggered is True
        assert result.severity == "HIGH"
        assert result.rule_id  == "AML-08"

    def test_no_trigger_on_domestic_large(self):
        result = check_cross_border_high_value(150_000, is_international=False)
        assert result.triggered is False

    def test_no_trigger_below_threshold(self):
        result = check_cross_border_high_value(50_000, is_international=True)
        assert result.triggered is False

    def test_threshold_boundary(self):
        result = check_cross_border_high_value(CROSS_BORDER_THRESHOLD_AED, True)
        assert result.triggered is True


# ── Payment AML Rule 9: PEP ────────────────────────────────────────────────

class TestPEPRule:
    def test_triggers_for_pep_customer(self):
        result = check_pep(is_pep=True, pep_category="Senior Government Official")
        assert result.triggered is True
        assert result.severity == "HIGH"
        assert result.rule_id  == "AML-09"

    def test_no_trigger_for_non_pep(self):
        result = check_pep(is_pep=False)
        assert result.triggered is False

    def test_result_has_fatf_basis(self):
        result = check_pep(is_pep=True)
        assert "FATF" in result.regulatory_basis
        assert "Recommendation 12" in result.regulatory_basis


# ── Payment AML Rule 10: Dormant Account ──────────────────────────────────

class TestDormantAccountRule:
    def test_triggers_on_dormant_account_large_amount(self):
        result = check_dormant_account(days_since_last_activity=200, amount_aed=20_000)
        assert result.triggered is True
        assert result.rule_id  == "AML-10"

    def test_no_trigger_on_active_account(self):
        result = check_dormant_account(days_since_last_activity=10, amount_aed=20_000)
        assert result.triggered is False

    def test_no_trigger_on_dormant_small_amount(self):
        result = check_dormant_account(days_since_last_activity=200, amount_aed=100)
        assert result.triggered is False

    def test_dormant_threshold_boundary(self):
        result = check_dormant_account(days_since_last_activity=DORMANT_DAYS, amount_aed=10_000)
        assert result.triggered is True


# ── Payment AML Rule 11: High-Risk MCC ────────────────────────────────────

class TestHighRiskMCCRule:
    def test_triggers_on_gambling_mcc(self):
        result = check_high_risk_merchant("7995", amount_aed=5_000)
        assert result.triggered is True
        assert result.rule_id  == "AML-11"

    def test_triggers_on_crypto_exchange_mcc(self):
        result = check_high_risk_merchant("6051", amount_aed=1_000)
        assert result.triggered is True

    def test_no_trigger_on_standard_merchant(self):
        result = check_high_risk_merchant("5411")  # Grocery store
        assert result.triggered is False

    def test_empty_mcc_no_trigger(self):
        result = check_high_risk_merchant("")
        assert result.triggered is False


# ── VARA Rule 12: Travel Rule ──────────────────────────────────────────────

class TestTravelRule:
    def test_triggers_above_threshold_missing_data(self):
        result = check_travel_rule(
            amount_aed      = 5_000,
            has_originator  = False,
            has_beneficiary = False,
        )
        assert result.triggered is True
        assert result.severity == "HIGH"
        assert result.rule_id  == "VARA-12"

    def test_no_trigger_with_complete_data(self):
        result = check_travel_rule(
            amount_aed      = 5_000,
            has_originator  = True,
            has_beneficiary = True,
        )
        assert result.triggered is False

    def test_no_trigger_below_threshold(self):
        result = check_travel_rule(amount_aed=1_000, has_originator=False)
        assert result.triggered is False

    def test_threshold_boundary(self):
        result = check_travel_rule(
            amount_aed      = TRAVEL_RULE_THRESHOLD_AED,
            has_originator  = False,
            has_beneficiary = True,
        )
        assert result.triggered is True

    def test_result_has_vara_basis(self):
        result = check_travel_rule(5_000, False, False)
        assert "VARA" in result.regulatory_basis


# ── VARA Rule 13: Unhosted Wallet ─────────────────────────────────────────

class TestUnhostedWalletRule:
    def test_triggers_on_unhosted_unverified(self):
        result = check_unhosted_wallet(
            wallet_type       = "unhosted",
            amount_aed        = 5_000,
            customer_verified = False,
        )
        assert result.triggered is True
        assert result.rule_id  == "VARA-13"

    def test_no_trigger_on_hosted_wallet(self):
        result = check_unhosted_wallet("hosted", 5_000, False)
        assert result.triggered is False

    def test_no_trigger_when_verified(self):
        result = check_unhosted_wallet("unhosted", 5_000, customer_verified=True)
        assert result.triggered is False


# ── VARA Rule 14: High-Risk VASP ──────────────────────────────────────────

class TestHighRiskVASPRule:
    def test_triggers_on_iran_vasp(self):
        result = check_high_risk_vasp("IR", "Iranian Exchange")
        assert result.triggered is True
        assert result.severity == "HIGH"
        assert result.rule_id  == "VARA-14"

    def test_no_trigger_on_low_risk_jurisdiction(self):
        result = check_high_risk_vasp("SG", "Singapore Exchange")
        assert result.triggered is False

    def test_triggers_on_north_korea(self):
        result = check_high_risk_vasp("KP")
        assert result.triggered is True


# ── VARA Rule 15: DeFi ────────────────────────────────────────────────────

class TestDeFiRule:
    def test_triggers_on_mixer_interaction(self):
        result = check_defi_interaction("MIXER", amount_aed=10_000)
        assert result.triggered is True
        assert result.severity == "CRITICAL"
        assert result.rule_id  == "VARA-15"

    def test_triggers_on_dex_interaction(self):
        result = check_defi_interaction("DEX", amount_aed=5_000)
        assert result.triggered is True

    def test_no_trigger_on_standard_protocol(self):
        result = check_defi_interaction("CEX", amount_aed=5_000)
        assert result.triggered is False


# ── VARA Rule 17: Stablecoin Cycling ──────────────────────────────────────

class TestStablecoinCyclingRule:
    def test_triggers_on_rapid_usdt_cycle(self):
        now = time.time()
        result = check_stablecoin_cycling(
            token_symbol = "USDT",
            inbound_ts   = now - 120,   # 2 minutes ago
            outbound_ts  = now,
            amount_aed   = 5_000,
        )
        assert result.triggered is True
        assert result.rule_id  == "VARA-17"

    def test_no_trigger_on_slow_cycle(self):
        now = time.time()
        result = check_stablecoin_cycling(
            token_symbol = "USDT",
            inbound_ts   = now - 3_600,  # 1 hour ago
            outbound_ts  = now,
            amount_aed   = 5_000,
        )
        assert result.triggered is False

    def test_no_trigger_on_eth(self):
        now = time.time()
        result = check_stablecoin_cycling("ETH", now - 60, now, 5_000)
        assert result.triggered is False

    def test_no_trigger_below_threshold(self):
        now = time.time()
        result = check_stablecoin_cycling("USDT", now - 60, now, 500)
        assert result.triggered is False


# ── Compliance engine integration ─────────────────────────────────────────

class TestComplianceEngine:
    def test_payment_check_returns_report(self):
        engine = ComplianceEngine()
        report = engine.check_payment_transaction(
            transaction_id = "TX-001",
            customer_id    = "CUST001",
            amount_aed     = 150_000,
            is_international = True,
            is_pep         = True,
        )
        assert report.rules_checked > 0
        assert report.composite_risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_vara_check_returns_report(self):
        engine = ComplianceEngine()
        report = engine.check_virtual_asset_transaction(
            transaction_id  = "TX-002",
            customer_id     = "CUST002",
            amount_aed      = 5_000,
            has_originator  = False,
            has_beneficiary = False,
            wallet_type     = "unhosted",
        )
        assert report.rules_checked > 0
        assert report.rules_triggered > 0

    def test_clean_transaction_returns_low_risk(self):
        engine = ComplianceEngine()
        report = engine.check_payment_transaction(
            transaction_id = "TX-003",
            customer_id    = "CUST003",
            amount_aed     = 1_000,
            is_pep         = False,
        )
        assert report.composite_risk == "LOW"
        assert report.rules_triggered == 0


# ── Compliance API endpoints ───────────────────────────────────────────────

class TestComplianceAPI:
    def test_rules_endpoint_returns_all_rules(self):
        r = client.get("/v1/compliance/rules")
        assert r.status_code == 200
        data = r.json()
        assert data["total_rules"] == 17
        assert len(data["rules"])  == 17

    def test_travel_rule_endpoint(self):
        r = client.get("/v1/compliance/travel-rule")
        assert r.status_code == 200
        data = r.json()
        assert "threshold_aed"   in data
        assert "threshold_usd"   in data
        assert data["threshold_usd"] == 1_000

    def test_vara_status_endpoint(self):
        r = client.get("/v1/compliance/vara/status")
        assert r.status_code == 200
        data = r.json()
        assert "rules_implemented" in data
        assert data["rules_implemented"] == 6

    def test_payment_compliance_check(self):
        r = client.post("/v1/compliance/check/payment", json={
            "transaction_id": "TX-TEST-001",
            "customer_id"   : "CUST001",
            "amount_aed"    : 150_000,
            "is_international": True,
            "is_pep"        : True,
        })
        assert r.status_code == 200
        data = r.json()
        assert "rules_triggered"  in data
        assert "composite_risk"   in data
        assert "triggered_rules"  in data
        assert data["rules_triggered"] > 0

    def test_vara_compliance_check(self):
        r = client.post("/v1/compliance/check/vara", json={
            "transaction_id" : "TX-TEST-002",
            "customer_id"    : "CUST002",
            "amount_aed"     : 5_000,
            "has_originator" : False,
            "has_beneficiary": False,
            "wallet_type"    : "unhosted",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["rules_triggered"] > 0
        assert data["composite_risk"] != "LOW"

    def test_payment_config_endpoint(self):
        r = client.get("/v1/compliance/payment/config")
        assert r.status_code == 200
        data = r.json()
        assert "structuring_threshold_aed"  in data
        assert "cross_border_threshold_aed" in data
        assert "high_risk_mcc_codes"        in data

    def test_clean_payment_returns_low_risk(self):
        r = client.post("/v1/compliance/check/payment", json={
            "transaction_id": "TX-TEST-003",
            "customer_id"   : "CUST003",
            "amount_aed"    : 500,
            "is_pep"        : False,
        })
        assert r.status_code == 200
        assert r.json()["composite_risk"] == "LOW"

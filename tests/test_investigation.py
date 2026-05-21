"""
tests/test_investigation.py — v3
─────────────────────────────────
Fixes from v2:
  1. test_skips_when_no_etherscan_key:
     get_settings is imported INSIDE the function body (local import),
     not at module level. Patching app.agents.crypto_agent.get_settings
     fails because the name is never bound at module scope.
     Fix: patch app.core.config.get_settings — the source of truth —
     which affects all callers including the local import.

  2. test_batch_no_eligible_alerts / test_batch_with_auto_closed:
     POST /batch was resolving as /{alert_id} with alert_id="batch"
     because /{alert_id} was registered first in investigation.py.
     Fix: investigation.py now registers /batch before /{alert_id}.
     Tests updated to match the corrected 400 response.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.graph.state import InvestigationState
from app.llm.client import LLMResponse
from app.main import app
from app.services.alert_store import store
from app.shared.models import (
    Alert, AlertStatus, AlertTrigger,
    KYCProfile, RiskBand, Transaction,
)

client = TestClient(app)

# ── Shared test data ──────────────────────────────────────────────────────────

MOCK_TX = Transaction(
    tx_id       = "TX-TEST-001",
    customer_id = "CUST001",
    amount_aed  = Decimal("55000.00"),
    currency    = "AED",
    merchant    = "Gulf Resources FZE",
    country     = "IR",
    timestamp   = datetime.utcnow(),
    is_flagged  = True,
)

MOCK_KYC = KYCProfile(
    customer_id       = "CUST001",
    name              = "Mohammed Al Rashidi",
    nationality       = "YE",
    account_age_days  = 15,
    device_id         = "dev-new",
    last_known_device = "dev-old",
    risk_tier         = RiskBand.HIGH,
)

MOCK_TRANSACTIONS = {"TX-TEST-001": MOCK_TX}
MOCK_KYC_PROFILES = {"CUST001": MOCK_KYC}

MOCK_SYNTHESIS_JSON = {
    "risk_score"            : 85,
    "risk_band"             : "HIGH",
    "investigation_summary" : (
        "High-value transfer to sanctioned corridor with KYC red flags. "
        "New account with device mismatch. Immediate investigation required."
    ),
    "key_concerns"          : ["AED 55k > CBUAE threshold", "Iran — FATF"],
    "recommendation"        : "ESCALATE",
    "regulatory_obligations": ["CBUAE STR required"],
    "confidence"            : "HIGH",
}


def _real_llm_response(data: dict) -> LLMResponse:
    """
    Real LLMResponse object — not a MagicMock.
    MemorySaver uses msgpack to checkpoint state. MagicMock is not
    msgpack-serialisable, so the graph crashes if it reaches the state.
    LLMResponse is a plain Python object — always use this in graph tests.
    """
    return LLMResponse(
        content    = json.dumps(data),
        provider   = "gemini",
        latency_ms = 1000.0,
        model      = "gemini-test",
    )


@pytest.fixture(autouse=True)
def clear():
    store.clear()
    yield
    store.clear()


def make_alert(**kw) -> Alert:
    a = Alert(
        tx_id       = kw.get("tx_id", "TX-TEST-001"),
        customer_id = kw.get("customer_id", "CUST001"),
        trigger     = kw.get("trigger", AlertTrigger.HIGH_VALUE),
        status      = kw.get("status", AlertStatus.INVESTIGATING),
    )
    store.save(a)
    return a


def base_state(**kw) -> InvestigationState:
    return {
        "alert_id"             : kw.get("alert_id", "test-alert-123"),
        "tx_id"                : kw.get("tx_id", "TX-TEST-001"),
        "customer_id"          : kw.get("customer_id", "CUST001"),
        "trigger"              : kw.get("trigger", "HIGH_VALUE"),
        "wallet_address"       : kw.get("wallet_address", None),
        "transaction_summary"  : None,
        "amount_aed"           : None,
        "country"              : None,
        "merchant"             : None,
        "findings"             : [],
        "risk_signals"         : [],
        "regulatory_flags"     : [],
        "crypto_signals"       : [],
        "final_risk_score"     : None,
        "final_risk_band"      : None,
        "investigation_summary": None,
        "recommendation"       : None,
        "crypto_risk_score"    : None,
        "hitl_decision"        : None,
        "hitl_analyst"         : None,
        "hitl_notes"           : None,
        "agents_completed"     : [],
        "errors"               : [],
    }


# ── State schema ──────────────────────────────────────────────────────────────

class TestInvestigationState:
    def test_required_fields_present(self):
        s = base_state()
        for field in ("alert_id", "tx_id", "customer_id", "findings",
                      "risk_signals", "agents_completed", "errors"):
            assert field in s

    def test_hitl_fields_for_phase5(self):
        s = base_state()
        assert "hitl_decision" in s
        assert "hitl_analyst"  in s
        assert "hitl_notes"    in s

    def test_crypto_fields_present(self):
        s = base_state()
        assert "wallet_address"    in s
        assert "crypto_risk_score" in s
        assert "crypto_signals"    in s


# ── Transaction agent ─────────────────────────────────────────────────────────

class TestTransactionAgent:
    @patch("app.agents.transaction_agent.load_transactions",
           return_value=MOCK_TRANSACTIONS)
    def test_generates_signals_for_high_value_ir(self, _):
        from app.agents.transaction_agent import transaction_agent
        result = transaction_agent(base_state())
        assert len(result["risk_signals"]) >= 2
        assert "transaction_agent" in result["agents_completed"]
        assert result["amount_aed"] == 55000.0
        assert result["country"]    == "IR"

    @patch("app.agents.transaction_agent.load_transactions",
           return_value=MOCK_TRANSACTIONS)
    def test_cbuae_regulatory_flag(self, _):
        from app.agents.transaction_agent import transaction_agent
        result = transaction_agent(base_state())
        assert any("CBUAE" in f for f in result["regulatory_flags"])

    @patch("app.agents.transaction_agent.load_transactions", return_value={})
    def test_missing_transaction_captured(self, _):
        from app.agents.transaction_agent import transaction_agent
        result = transaction_agent(base_state())
        assert len(result["errors"]) > 0
        assert "transaction_agent" in result["agents_completed"]

    @patch("app.agents.transaction_agent.load_transactions",
           return_value=MOCK_TRANSACTIONS)
    def test_returns_required_keys(self, _):
        from app.agents.transaction_agent import transaction_agent
        result = transaction_agent(base_state())
        for key in ("agents_completed", "findings", "risk_signals",
                    "regulatory_flags", "errors", "crypto_signals"):
            assert key in result


# ── KYC agent ─────────────────────────────────────────────────────────────────

class TestKYCAgent:
    @patch("app.agents.kyc_agent.load_kyc_profiles",
           return_value=MOCK_KYC_PROFILES)
    def test_detects_device_mismatch_and_new_account(self, _):
        from app.agents.kyc_agent import kyc_agent
        result  = kyc_agent(base_state())
        signals = " ".join(result["risk_signals"]).lower()
        assert any(kw in signals for kw in ("device", "mismatch", "new account", "15 days"))
        assert "kyc_agent" in result["agents_completed"]

    @patch("app.agents.kyc_agent.load_kyc_profiles", return_value={})
    def test_unknown_customer_flagged(self, _):
        from app.agents.kyc_agent import kyc_agent
        result  = kyc_agent(base_state())
        signals = " ".join(result["risk_signals"]).lower()
        assert "no kyc" in signals or "unverified" in signals

    @patch("app.agents.kyc_agent.load_kyc_profiles",
           return_value=MOCK_KYC_PROFILES)
    def test_high_risk_tier_flagged(self, _):
        from app.agents.kyc_agent import kyc_agent
        result  = kyc_agent(base_state())
        signals = " ".join(result["risk_signals"])
        assert "HIGH" in signals

    @patch("app.agents.kyc_agent.load_kyc_profiles",
           return_value=MOCK_KYC_PROFILES)
    def test_returns_required_keys(self, _):
        from app.agents.kyc_agent import kyc_agent
        result = kyc_agent(base_state())
        for key in ("agents_completed", "findings", "risk_signals",
                    "regulatory_flags", "errors", "crypto_signals"):
            assert key in result


# ── Sanctions agent ───────────────────────────────────────────────────────────

class TestSanctionsAgent:
    def _run_with_mock_screener(self, state):
        from app.agents.sanctions_agent import sanctions_agent
        from app.services.sanctions_screener import SanctionsScreener

        mock_r = MagicMock(is_hit=False, best_score=0, matches=[])

        with patch("app.agents.sanctions_agent.SanctionsScreener") as MockCls, \
             patch("app.agents.sanctions_agent.load_kyc_profiles", return_value={}):
            mock_inst                  = MagicMock()
            mock_inst.screen.return_value = mock_r
            mock_inst.entity_count     = 5
            mock_inst.name_variant_count = 15
            MockCls.return_value       = mock_inst
            return sanctions_agent(state)

    def test_returns_required_keys(self):
        state             = base_state()
        state["merchant"] = "Dubai Mall"
        state["country"]  = "AE"
        result = self._run_with_mock_screener(state)
        for key in ("agents_completed", "findings", "risk_signals",
                    "regulatory_flags", "errors"):
            assert key in result

    def test_agent_completed(self):
        state             = base_state()
        state["merchant"] = "Dubai Mall"
        state["country"]  = "AE"
        result = self._run_with_mock_screener(state)
        assert "sanctions_agent" in result["agents_completed"]


# ── Crypto agent ──────────────────────────────────────────────────────────────

class TestCryptoAgent:
    def test_skips_when_no_wallet(self):
        from app.agents.crypto_agent import crypto_agent
        result = crypto_agent(base_state(wallet_address=None))
        assert "crypto_agent" in result["agents_completed"]
        assert any(f.get("status") == "skipped" for f in result["findings"])

    def test_skips_when_no_etherscan_key(self):
        """
        Fix: get_settings is imported inside the function body, not at module
        level. Patching app.agents.crypto_agent.get_settings fails because
        the name is never bound at module scope.

        Correct fix: patch app.core.config.get_settings — this affects the
        local import inside the function since Python resolves it at call time.
        """
        from app.agents.crypto_agent import crypto_agent

        mock_settings                  = MagicMock()
        mock_settings.has_etherscan_key = False

        with patch("app.core.config.get_settings", return_value=mock_settings):
            state                  = base_state()
            state["wallet_address"] = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
            result = crypto_agent(state)

        assert "crypto_agent" in result["agents_completed"]
        combined = " ".join(
            result.get("risk_signals", []) +
            [f.get("reason", "") + f.get("status", "") for f in result.get("findings", [])]
        )
        assert "ETHERSCAN" in combined.upper() or "skipped" in combined.lower()


# ── Synthesis agent ───────────────────────────────────────────────────────────

class TestSynthesisAgent:
    @patch("app.agents.synthesis_agent.get_llm_client")
    def test_updates_alert_score_and_status(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.complete.return_value = _real_llm_response(MOCK_SYNTHESIS_JSON)
        mock_get_llm.return_value      = mock_llm

        alert = make_alert()
        state = base_state(alert_id=str(alert.alert_id))
        state["risk_signals"]     = ["High value", "Sanctioned corridor"]
        state["agents_completed"] = ["transaction_agent", "kyc_agent"]

        from app.agents.synthesis_agent import synthesis_agent
        result = synthesis_agent(state)

        assert result["final_risk_score"] == 85
        assert result["final_risk_band"]  == "HIGH"
        assert result["recommendation"]   == "ESCALATE"
        assert "synthesis_agent" in result["agents_completed"]

    @patch("app.agents.synthesis_agent.get_llm_client")
    def test_fallback_when_llm_fails(self, mock_get_llm):
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM unavailable")
        mock_get_llm.return_value     = mock_llm

        alert = make_alert()
        state = base_state(alert_id=str(alert.alert_id))
        state["risk_signals"] = ["Signal 1", "Signal 2", "Signal 3"]

        from app.agents.synthesis_agent import synthesis_agent
        result = synthesis_agent(state)

        assert result["final_risk_score"] is not None
        assert result["recommendation"]   is not None
        assert len(result["errors"]) > 0


# ── Graph ─────────────────────────────────────────────────────────────────────

class TestInvestigationGraph:
    def test_graph_builds_without_error(self):
        from app.graph.investigation_graph import build_investigation_graph
        assert build_investigation_graph() is not None

    @patch("app.agents.synthesis_agent.get_llm_client")
    @patch("app.agents.kyc_agent.load_kyc_profiles",
           return_value=MOCK_KYC_PROFILES)
    @patch("app.agents.transaction_agent.load_transactions",
           return_value=MOCK_TRANSACTIONS)
    def test_full_graph_run(self, _tx, _kyc, mock_get_llm):
        """
        Fix: real LLMResponse — MemorySaver needs msgpack-serialisable state.
        Fix: patch SanctionsScreener at the class level in the sanctions module
        so entity_count/name_variant_count are plain ints, not MagicMock attrs.
        """
        mock_llm = MagicMock()
        mock_llm.complete.return_value = _real_llm_response(MOCK_SYNTHESIS_JSON)
        mock_get_llm.return_value      = mock_llm

        alert = make_alert()

        mock_screen_result = MagicMock(is_hit=False, best_score=0, matches=[])

        with patch("app.agents.sanctions_agent.SanctionsScreener") as MockScreener, \
             patch("app.agents.sanctions_agent.load_kyc_profiles",
                   return_value=MOCK_KYC_PROFILES):
            mock_inst                    = MagicMock()
            mock_inst.screen.return_value = mock_screen_result
            mock_inst.entity_count       = 5      # plain int — serialisable
            mock_inst.name_variant_count = 15     # plain int — serialisable
            MockScreener.return_value    = mock_inst

            from app.graph.investigation_graph import (
                build_investigation_graph, run_investigation,
            )
            graph  = build_investigation_graph()
            result = run_investigation(
                graph       = graph,
                alert_id    = str(alert.alert_id),
                tx_id       = alert.tx_id,
                customer_id = alert.customer_id,
                trigger     = alert.trigger.value,
            )

        assert result["final_risk_score"] == 85
        assert result["recommendation"]   == "ESCALATE"
        assert "transaction_agent" in result["agents_completed"]
        assert "kyc_agent"         in result["agents_completed"]
        assert "synthesis_agent"   in result["agents_completed"]


# ── Investigation API ─────────────────────────────────────────────────────────

class TestInvestigationAPI:
    def test_stats_returns_200(self):
        assert client.get("/v1/investigate/stats").status_code == 200

    def test_stats_has_required_fields(self):
        data = client.get("/v1/investigate/stats").json()
        assert "total_alerts"       in data
        assert "investigated_count" in data
        assert "graph_compiled"     in data

    def test_investigate_unknown_alert_returns_404(self):
        r = client.post(
            "/v1/investigate/00000000-0000-0000-0000-000000000000",
            json={},
        )
        assert r.status_code == 404

    def test_get_result_not_investigated_returns_400(self):
        alert = make_alert()
        r     = client.get(f"/v1/investigate/{alert.alert_id}/result")
        assert r.status_code == 400

    def test_batch_no_eligible_alerts_returns_400(self):
        """
        Fix: investigation.py now registers /batch before /{alert_id}.
        POST /batch no longer resolves as alert_id="batch" → 404.
        Empty store → no eligible alerts → 400.
        """
        assert store.count() == 0
        r = client.post("/v1/investigate/batch", json={"max_alerts": 3})
        assert r.status_code == 400

    def test_batch_only_auto_closed_returns_400(self):
        """AUTO_CLOSED alerts are not eligible — should still return 400."""
        alert        = make_alert()
        alert.status = AlertStatus.AUTO_CLOSED
        store.save(alert)
        r = client.post("/v1/investigate/batch", json={"max_alerts": 3})
        assert r.status_code == 400

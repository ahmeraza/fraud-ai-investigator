"""
tests/test_crypto.py
─────────────────────
Tests for the crypto monitoring module.

All tests run offline — no Etherscan API calls, no internet needed.
EtherscanClient is mocked so tests cost $0 and run in milliseconds.

Coverage:
  - MixerDetector: known address detection, behavioural patterns, scoring
  - EtherscanClient: rate limiting, response parsing, error handling
  - CryptoAlertEngine: alert creation, audit logging, error capture
  - Crypto API endpoints: screen, batch, mixers, status

Run:
    uv run pytest tests/test_crypto.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.crypto.mixer_detector import (
    MixerDetector,
    SANCTIONED_MIXER_ADDRESSES,
)
from app.main import app
from app.services.alert_store import store

client = TestClient(app)

# ── Shared test data ──────────────────────────────────────────────────────────

# The wallet being analysed — a customer's address
CUSTOMER_ADDR     = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"

# Known Tornado Cash router — OFAC sanctioned
TORNADO_CASH_ADDR = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"

# Transaction FROM customer TO Tornado Cash — direct mixer interaction
TX_TO_MIXER = {
    "hash"     : "0xabc123",
    "from"     : CUSTOMER_ADDR,       # ← wallet being screened sends...
    "to"       : TORNADO_CASH_ADDR,   # ← ...to known sanctioned mixer
    "value"    : str(int(1.0 * 1e18)),# 1 ETH exactly (Tornado Cash denomination)
    "timeStamp": "1700000000",
    "gas"      : "21000",
    "gasPrice" : "20000000000",
    "isError"  : "0",
}

# Clean transaction — non-mixer counterparty
TX_NORMAL = {
    "hash"     : "0xdef456",
    "from"     : CUSTOMER_ADDR,
    "to"       : "0x1234567890123456789012345678901234567890",
    "value"    : str(int(0.137 * 1e18)),  # 0.137 ETH — not a round amount
    "timeStamp": "1700003600",
    "gas"      : "21000",
    "gasPrice" : "15000000000",
    "isError"  : "0",
}

# Rapid in/out: funds received then sent within 30 minutes
TX_RECEIVE = {
    "hash"     : "0xreceive",
    "from"     : "0x9999999999999999999999999999999999999999",
    "to"       : CUSTOMER_ADDR,          # ← customer receives funds
    "value"    : str(int(5.0 * 1e18)),
    "timeStamp": "1700000000",
    "isError"  : "0",
}
TX_SEND_RAPID = {
    "hash"     : "0xsendrapid",
    "from"     : CUSTOMER_ADDR,          # ← customer sends funds 30 min later
    "to"       : "0x8888888888888888888888888888888888888888",
    "value"    : str(int(4.9 * 1e18)),
    "timeStamp": "1700001800",           # 30 minutes after receive
    "isError"  : "0",
}


@pytest.fixture(autouse=True)
def clear():
    store.clear()
    yield
    store.clear()


# ── MixerDetector tests ───────────────────────────────────────────────────────

class TestMixerDetector:
    def setup_method(self):
        self.detector = MixerDetector(score_threshold=60)

    def test_direct_mixer_interaction_flags_address(self):
        """
        Customer sends to Tornado Cash Router.
        The customer's wallet (CUSTOMER_ADDR) should be flagged,
        not the mixer's address — we are screening the customer.
        """
        result = self.detector.analyse(
            address            = CUSTOMER_ADDR,    # ← wallet being screened
            transactions       = [TX_TO_MIXER],    # ← contains TC counterparty
            token_transactions = [],
            eth_balance        = 1.0,
        )
        assert result.is_flagged, (
            f"Expected flagged=True but got score={result.risk_score}. "
            f"Check that TX_TO_MIXER has TORNADO_CASH_ADDR in 'to' field "
            f"and CUSTOMER_ADDR is the screened wallet."
        )
        assert result.risk_score >= 60
        assert len(result.direct_hits) >= 1
        assert result.direct_hits[0]["mixer_name"] == "Tornado Cash Router"

    def test_direct_hit_signal_type(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_TO_MIXER],
            token_transactions=[], eth_balance=0.0,
        )
        signal_types = [s.signal_type for s in result.signals]
        assert "DIRECT_MIXER_INTERACTION" in signal_types

    def test_clean_address_not_flagged(self):
        """Normal transaction to non-mixer — should not be flagged."""
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_NORMAL],
            token_transactions=[], eth_balance=1.5,
        )
        assert not result.is_flagged
        assert result.risk_score < 60

    def test_empty_transactions_not_flagged(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[],
            token_transactions=[], eth_balance=0.0,
        )
        assert not result.is_flagged
        assert result.risk_score == 0

    def test_round_amount_pattern_detected(self):
        """Multiple 1 ETH transactions (Tornado Cash denomination) raises score."""
        round_txs = [
            {**TX_NORMAL, "hash": f"0xround{i}", "value": str(int(1.0 * 1e18))}
            for i in range(5)
        ]
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=round_txs,
            token_transactions=[], eth_balance=5.0,
        )
        signal_types = [s.signal_type for s in result.signals]
        assert "ROUND_AMOUNT_PATTERN" in signal_types

    def test_rapid_in_out_detected(self):
        """Receive then send within 30 minutes triggers layering signal."""
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_RECEIVE, TX_SEND_RAPID],
            token_transactions=[], eth_balance=0.1,
        )
        signal_types = [s.signal_type for s in result.signals]
        assert "RAPID_IN_OUT_PATTERN" in signal_types

    def test_severity_high_on_direct_mixer_hit(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_TO_MIXER],
            token_transactions=[], eth_balance=0.0,
        )
        assert result.severity in ("HIGH", "CRITICAL")

    def test_to_dict_has_required_fields(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_TO_MIXER],
            token_transactions=[], eth_balance=1.0,
        )
        d        = result.to_dict()
        required = {
            "address", "risk_score", "severity", "is_flagged",
            "recommended_action", "direct_hits", "signals",
            "transaction_count", "screening_ms",
        }
        assert required.issubset(d.keys())

    def test_result_has_timing(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_NORMAL],
            token_transactions=[], eth_balance=0.0,
        )
        assert result.screening_ms > 0

    def test_score_capped_at_100(self):
        many_mixer_txs = [TX_TO_MIXER] * 20
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=many_mixer_txs,
            token_transactions=[], eth_balance=100.0,
        )
        assert result.risk_score <= 100

    def test_round_eth_amount_detection(self):
        # Tornado Cash fixed denominations → should be detected as round
        assert MixerDetector._is_round_eth_amount(str(int(0.1  * 1e18)))
        assert MixerDetector._is_round_eth_amount(str(int(1.0  * 1e18)))
        assert MixerDetector._is_round_eth_amount(str(int(10.0 * 1e18)))
        # Non-round amounts → should NOT be detected
        assert not MixerDetector._is_round_eth_amount(str(int(0.137  * 1e18)))
        assert not MixerDetector._is_round_eth_amount(str(int(3.14159 * 1e18)))

    def test_recommended_action_on_direct_hit(self):
        result = self.detector.analyse(
            address=CUSTOMER_ADDR, transactions=[TX_TO_MIXER],
            token_transactions=[], eth_balance=0.0,
        )
        assert result.recommended_action in (
            "BLOCK_AND_REPORT", "ESCALATE_COMPLIANCE"
        )


# ── Known mixer addresses ─────────────────────────────────────────────────────

class TestKnownMixers:
    def test_tornado_cash_router_in_list(self):
        assert TORNADO_CASH_ADDR in SANCTIONED_MIXER_ADDRESSES

    def test_all_addresses_are_lowercase(self):
        for addr in SANCTIONED_MIXER_ADDRESSES:
            assert addr == addr.lower(), f"Not lowercase: {addr}"

    def test_all_addresses_start_with_0x(self):
        for addr in SANCTIONED_MIXER_ADDRESSES:
            assert addr.startswith("0x")

    def test_all_entries_have_required_fields(self):
        required = {"name", "sanction", "date", "notes"}
        for addr, info in SANCTIONED_MIXER_ADDRESSES.items():
            assert required.issubset(info.keys()), f"Missing fields for {addr}"

    def test_minimum_mixer_count(self):
        assert len(SANCTIONED_MIXER_ADDRESSES) >= 5


# ── EtherscanClient tests (mocked) ────────────────────────────────────────────

class TestEtherscanClient:
    def _make_client(self):
        from app.crypto.etherscan_client import EtherscanClient
        return EtherscanClient(api_key="test-key-123", chain_id=1)

    @patch("app.crypto.etherscan_client.requests.get")
    def test_get_transactions_returns_list(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "1", "message": "OK", "result": [TX_NORMAL]},
        )
        txs = self._make_client().get_transactions(CUSTOMER_ADDR, limit=10)
        assert isinstance(txs, list)
        assert len(txs) == 1

    @patch("app.crypto.etherscan_client.requests.get")
    def test_no_transactions_returns_empty_list(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "0", "message": "No transactions found", "result": []},
        )
        assert self._make_client().get_transactions(CUSTOMER_ADDR) == []

    @patch("app.crypto.etherscan_client.requests.get")
    def test_get_eth_balance_converts_from_wei(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "1", "message": "OK", "result": str(int(5.0 * 1e18))},
        )
        balance = self._make_client().get_eth_balance(CUSTOMER_ADDR)
        assert abs(balance - 5.0) < 0.0001

    def test_raises_without_api_key(self):
        from app.crypto.etherscan_client import EtherscanClient
        with pytest.raises(RuntimeError, match="API key"):
            EtherscanClient(api_key="")

    @patch("app.crypto.etherscan_client.requests.get")
    def test_api_error_returns_empty_list(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "0", "message": "Invalid API Key", "result": ""},
        )
        assert self._make_client().get_transactions(CUSTOMER_ADDR) == []


# ── CryptoAlertEngine tests (mocked) ─────────────────────────────────────────

class TestCryptoAlertEngine:

    @patch("app.crypto.crypto_alert_engine.EtherscanClient")
    def test_flagged_address_creates_alert(self, MockClient):
        """
        Customer wallet has sent to Tornado Cash — engine should flag and create alert.
        The mock returns TX_TO_MIXER which contains the TC address as counterparty.
        """
        mock_instance = MagicMock()
        mock_instance.get_transactions.return_value       = [TX_TO_MIXER]
        mock_instance.get_token_transfers.return_value    = []
        mock_instance.get_eth_balance.return_value        = 1.0
        mock_instance.get_internal_transactions.return_value = []
        MockClient.return_value = mock_instance

        from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
        engine  = CryptoAlertEngine(etherscan_api_key="test-key", score_threshold=60)
        request = CryptoScreeningRequest(
            address     = CUSTOMER_ADDR,  # ← screen the customer, not TC
            customer_id = "CUST001",
        )
        response = engine.screen_address(request)
        assert response.alert_created, (
            f"Expected alert_created=True but got {response.detection_result}"
        )
        assert response.alert_id != ""
        assert store.count() > 0

    @patch("app.crypto.crypto_alert_engine.EtherscanClient")
    def test_clean_address_no_alert(self, MockClient):
        mock_instance = MagicMock()
        mock_instance.get_transactions.return_value       = [TX_NORMAL]
        mock_instance.get_token_transfers.return_value    = []
        mock_instance.get_eth_balance.return_value        = 0.5
        mock_instance.get_internal_transactions.return_value = []
        MockClient.return_value = mock_instance

        from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
        engine   = CryptoAlertEngine(etherscan_api_key="test-key", score_threshold=60)
        request  = CryptoScreeningRequest(address=CUSTOMER_ADDR, customer_id="CUST002")
        response = engine.screen_address(request)
        assert not response.alert_created
        assert store.count() == 0

    @patch("app.crypto.crypto_alert_engine.EtherscanClient")
    def test_audit_event_logged_on_flag(self, MockClient):
        mock_instance = MagicMock()
        mock_instance.get_transactions.return_value       = [TX_TO_MIXER]
        mock_instance.get_token_transfers.return_value    = []
        mock_instance.get_eth_balance.return_value        = 1.0
        mock_instance.get_internal_transactions.return_value = []
        MockClient.return_value = mock_instance

        from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
        engine   = CryptoAlertEngine(etherscan_api_key="test-key", score_threshold=60)
        request  = CryptoScreeningRequest(address=CUSTOMER_ADDR, customer_id="CUST003")
        response = engine.screen_address(request)

        if response.alert_created:
            events = store.get_audit_trail(response.alert_id)
            types  = [e.event_type for e in events]
            assert "CRYPTO_ALERT_CREATED" in types

    @patch("app.crypto.crypto_alert_engine.EtherscanClient")
    def test_engine_error_captured_gracefully(self, MockClient):
        MockClient.side_effect = RuntimeError("Network timeout")

        from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
        engine   = CryptoAlertEngine(etherscan_api_key="test-key", score_threshold=60)
        request  = CryptoScreeningRequest(address=CUSTOMER_ADDR)
        response = engine.screen_address(request)
        assert response.error != ""
        assert not response.alert_created


# ── Crypto API endpoint tests ─────────────────────────────────────────────────

class TestCryptoAPI:
    def test_mixers_endpoint_returns_list(self):
        response = client.get("/v1/crypto/mixers")
        assert response.status_code == 200
        data = response.json()
        assert "count"  in data
        assert "mixers" in data
        assert data["count"] >= 5

    def test_mixers_endpoint_has_tornado_cash(self):
        mixers = client.get("/v1/crypto/mixers").json()["mixers"]
        assert any("Tornado" in m["name"] for m in mixers)

    def test_status_endpoint_returns_200(self):
        assert client.get("/v1/crypto/status").status_code == 200

    def test_status_has_required_fields(self):
        data     = client.get("/v1/crypto/status").json()
        required = {
            "etherscan_api_configured",
            "score_threshold",
            "known_mixer_addresses",
            "supported_chains",
        }
        assert required.issubset(data.keys())

    def test_screen_invalid_address_returns_422(self):
        response = client.post(
            "/v1/crypto/screen",
            json={"address": "not-a-valid-address"},
        )
        assert response.status_code == 422

    def test_batch_empty_addresses_returns_422(self):
        response = client.post(
            "/v1/crypto/screen/batch",
            json={"addresses": []},
        )
        assert response.status_code == 422

    def test_mixer_entries_have_required_fields(self):
        mixers = client.get("/v1/crypto/mixers").json()["mixers"]
        for mixer in mixers:
            for field in ("address", "name", "sanction", "date_listed"):
                assert field in mixer

    def test_supported_chains_in_status(self):
        data   = client.get("/v1/crypto/status").json()
        chains = data.get("supported_chains", {})
        assert "1" in chains       # Ethereum
        assert "137" in chains     # Polygon

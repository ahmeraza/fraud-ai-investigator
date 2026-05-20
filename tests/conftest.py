"""
tests/conftest.py
──────────────────
Shared pytest fixtures available to all test modules.

Fixtures defined here are automatically discovered by pytest
without needing to import them in test files.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.models import (
    Alert,
    AlertTrigger,
    KYCProfile,
    RiskBand,
    SanctionsEntry,
    Transaction,
)


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client — use for HTTP endpoint tests."""
    return TestClient(app)


@pytest.fixture
def sample_transaction() -> Transaction:
    """A standard low-risk transaction for testing."""
    return Transaction(
        tx_id="TX-TEST-001",
        customer_id="CUST001",
        amount_aed=Decimal("5000.00"),
        currency="AED",
        merchant="Abu Dhabi Mall",
        country="AE",
        timestamp=datetime.utcnow(),
        is_flagged=False,
    )


@pytest.fixture
def suspicious_transaction() -> Transaction:
    """A high-value, high-risk transaction for testing fraud rules."""
    return Transaction(
        tx_id="TX-TEST-002",
        customer_id="CUST005",
        amount_aed=Decimal("250000.00"),
        currency="AED",
        merchant="Shell Holdings International Ltd",
        country="IR",
        timestamp=datetime.utcnow(),
        is_flagged=True,
    )


@pytest.fixture
def clean_kyc_profile() -> KYCProfile:
    """KYC profile with no risk signals."""
    return KYCProfile(
        customer_id="CUST001",
        name="Ahmed Al Mansoori",
        nationality="AE",
        account_age_days=730,
        device_id="device-stable-abc-123",
        last_known_device="device-stable-abc-123",  # same = no mismatch
        risk_tier=RiskBand.LOW,
    )


@pytest.fixture
def suspicious_kyc_profile() -> KYCProfile:
    """KYC profile with device mismatch and new account signals."""
    return KYCProfile(
        customer_id="CUST005",
        name="Test User",
        nationality="AE",
        account_age_days=5,  # very new
        device_id="device-new-xyz-999",
        last_known_device="device-old-abc-111",  # mismatch
        risk_tier=RiskBand.HIGH,
    )


@pytest.fixture
def sanctions_watchlist() -> list[SanctionsEntry]:
    """Small sanctions list for testing matching logic."""
    return [
        SanctionsEntry(
            name="Shell Holdings International Ltd",
            aliases=["Shell Holdings Intl", "SHI Ltd"],
            country="IR",
            reason="OFAC SDN",
        ),
        SanctionsEntry(
            name="Mohamed Al Rashid Trading",
            aliases=["Mohammed Al Rashid Trading", "Muhammad Rashid Trading"],
            country="YE",
            reason="OFAC — terrorism financing",
        ),
    ]


@pytest.fixture
def pending_alert() -> Alert:
    """A newly created alert in PENDING state."""
    return Alert(
        tx_id="TX-TEST-001",
        customer_id="CUST001",
        trigger=AlertTrigger.HIGH_VALUE,
    )

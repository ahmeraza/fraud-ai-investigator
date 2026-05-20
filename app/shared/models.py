"""
app/shared/models.py
─────────────────────
All Pydantic data models shared across the application.

These are the core domain objects — every layer (API, services, agents)
imports from here rather than defining its own schemas.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ────────────────────────────────────────────────────────────


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    PENDING = "PENDING"           # Just created, not yet triaged
    TRIAGING = "TRIAGING"         # Triage agent is running
    INVESTIGATING = "INVESTIGATING"  # Multi-agent investigation running
    AWAITING_HUMAN = "AWAITING_HUMAN"  # Waiting for analyst decision
    FRAUD_CONFIRMED = "FRAUD_CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    AUTO_CLOSED = "AUTO_CLOSED"   # Low risk, closed automatically


class AlertTrigger(str, Enum):
    HIGH_VALUE = "HIGH_VALUE"           # Amount exceeds reporting threshold
    SANCTIONED_CORRIDOR = "SANCTIONED_CORRIDOR"  # High-risk country
    DEVICE_MISMATCH = "DEVICE_MISMATCH"  # KYC device fingerprint mismatch
    VELOCITY = "VELOCITY"               # Too many transactions in short window
    NEW_ACCOUNT = "NEW_ACCOUNT"         # Account < 30 days old
    MANUAL = "MANUAL"                   # Manually raised by analyst


class Verdict(str, Enum):
    FRAUD_CONFIRMED = "FRAUD_CONFIRMED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


# ── Core domain models ───────────────────────────────────────────────────────


class Transaction(BaseModel):
    """A single financial transaction, the primary input to the system."""

    tx_id: str = Field(description="Unique transaction identifier")
    customer_id: str = Field(description="Internal customer reference")
    amount_aed: Decimal = Field(gt=0, description="Transaction amount in AED")
    currency: str = Field(default="AED", max_length=3)
    merchant: str = Field(description="Merchant or counterparty name")
    country: str = Field(
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code of the transaction",
    )
    timestamp: datetime = Field(description="UTC timestamp of the transaction")
    is_flagged: bool = Field(
        default=False,
        description="Ground truth label (used in evaluation only)",
    )

    @field_validator("country")
    @classmethod
    def country_uppercase(cls, v: str) -> str:
        return v.upper()

    @field_validator("currency")
    @classmethod
    def currency_uppercase(cls, v: str) -> str:
        return v.upper()

    @property
    def amount_float(self) -> float:
        return float(self.amount_aed)

    model_config = {"json_encoders": {Decimal: str}}


class KYCProfile(BaseModel):
    """Know Your Customer profile — identity and risk attributes for a customer."""

    customer_id: str
    name: str
    nationality: str = Field(min_length=2, max_length=2)
    account_age_days: int = Field(ge=0)
    device_id: str = Field(description="Current device fingerprint")
    last_known_device: str = Field(description="Device from last successful KYC")
    risk_tier: RiskBand = Field(default=RiskBand.LOW)

    @property
    def has_device_mismatch(self) -> bool:
        return self.device_id != self.last_known_device

    @property
    def is_new_account(self) -> bool:
        return self.account_age_days < 30


class SanctionsEntry(BaseModel):
    """A single entry from the sanctions / watchlist database."""

    name: str = Field(description="Entity name as it appears on the watchlist")
    country: str = Field(min_length=2, max_length=2)
    reason: str = Field(description="Sanctions regime, e.g. OFAC SDN, UN, EU")
    aliases: list[str] = Field(
        default_factory=list,
        description="Known alternate spellings and transliterations",
    )


# ── Alert models ─────────────────────────────────────────────────────────────


class Alert(BaseModel):
    """
    A fraud alert — the central object that flows through the entire pipeline.
    Created by the alert engine and enriched by each subsequent stage.
    """

    alert_id: UUID = Field(default_factory=uuid4)
    tx_id: str
    customer_id: str
    trigger: AlertTrigger
    status: AlertStatus = Field(default=AlertStatus.PENDING)
    risk_score: Optional[int] = Field(
        default=None,
        ge=0,
        le=100,
        description="Risk score 0–100 assigned after investigation",
    )
    risk_band: Optional[RiskBand] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Enrichment fields — populated progressively by each pipeline stage
    triage_narrative: Optional[str] = None
    investigation_summary: Optional[str] = None
    analyst_notes: Optional[str] = None
    resolution_narrative: Optional[str] = None

    def model_post_init(self, __context: object) -> None:
        # Keep updated_at in sync whenever the model is constructed/updated
        self.updated_at = datetime.utcnow()


class AlertCreateRequest(BaseModel):
    """Request body for POST /v1/alerts — manually submit a transaction for review."""

    tx_id: str
    customer_id: str
    trigger: AlertTrigger = Field(default=AlertTrigger.MANUAL)


class AlertResponse(BaseModel):
    """API response shape for a single alert."""

    alert_id: str
    tx_id: str
    customer_id: str
    trigger: AlertTrigger
    status: AlertStatus
    risk_score: Optional[int]
    risk_band: Optional[RiskBand]
    created_at: datetime
    triage_narrative: Optional[str]
    investigation_summary: Optional[str]

    @classmethod
    def from_alert(cls, alert: Alert) -> "AlertResponse":
        return cls(
            alert_id=str(alert.alert_id),
            tx_id=alert.tx_id,
            customer_id=alert.customer_id,
            trigger=alert.trigger,
            status=alert.status,
            risk_score=alert.risk_score,
            risk_band=alert.risk_band,
            created_at=alert.created_at,
            triage_narrative=alert.triage_narrative,
            investigation_summary=alert.investigation_summary,
        )


# ── HITL (Human-in-the-Loop) models ─────────────────────────────────────────


class HITLDecisionRequest(BaseModel):
    """Request body for POST /v1/hitl/{alert_id}/decision."""

    verdict: Verdict
    analyst_notes: str = Field(
        min_length=10,
        description="Analyst must provide reasoning — minimum 10 characters",
    )
    analyst_id: str = Field(description="Analyst user ID or name")


# ── Audit models ─────────────────────────────────────────────────────────────


class AuditEvent(BaseModel):
    """A single timestamped event in the case audit trail."""

    event_id: UUID = Field(default_factory=uuid4)
    alert_id: str
    event_type: str = Field(description="e.g. ALERT_CREATED, TRIAGE_COMPLETE, HUMAN_DECISION")
    description: str
    actor: str = Field(description="System component or analyst ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict = Field(default_factory=dict)


# ── Health check model ───────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    llm_providers: dict[str, bool] = Field(
        description="Which LLM providers have valid API keys configured"
    )

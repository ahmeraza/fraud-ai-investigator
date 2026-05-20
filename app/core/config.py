"""
app/core/config.py
──────────────────
Centralised application settings loaded from environment variables.
Uses pydantic-settings so every variable is typed and validated at startup.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All application settings — sourced from .env file or environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App metadata ────────────────────────────────────────────────────────
    app_env: str = Field(default="development", description="development | production")
    app_version: str = Field(default="0.1.0")
    log_level: str = Field(default="INFO")

    # ── LLM API keys ────────────────────────────────────────────────────────
    gemini_api_key: str = Field(default="", description="Google Gemini API key")
    groq_api_key: str = Field(default="", description="Groq API key (fallback LLM)")

    # ── LLM model names ─────────────────────────────────────────────────────
    gemini_model: str = Field(default="gemini-2.5-flash-preview-05-20")
    groq_model: str = Field(default="llama3-70b-8192")

    # ── Fraud thresholds (AED amounts) ──────────────────────────────────────
    high_value_threshold_aed: float = Field(
        default=40_000.0,
        description="UAE Central Bank reporting threshold in AED",
    )
    critical_amount_threshold_aed: float = Field(
        default=200_000.0,
        description="Auto-escalate above this AED amount",
    )

    # ── Risk scoring bands ──────────────────────────────────────────────────
    risk_band_low: int = Field(default=30, description="Below this = LOW risk")
    risk_band_medium: int = Field(default=70, description="Below this = MEDIUM risk")
    risk_band_high: int = Field(default=90, description="Below this = HIGH risk")
    # Above risk_band_high = CRITICAL

    # ── MENA high-risk jurisdictions (ISO 3166-1 alpha-2) ───────────────────
    high_risk_countries: list[str] = Field(
        default=["IR", "KP", "SY", "MM", "YE", "SD", "CU"],
        description="FATF and UN sanctioned / high-risk jurisdictions",
    )

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_groq_key(self) -> bool:
        return bool(self.groq_api_key)

    def risk_band_label(self, score: int) -> str:
        """Convert numeric risk score to band label."""
        if score < self.risk_band_low:
            return "LOW"
        if score < self.risk_band_medium:
            return "MEDIUM"
        if score < self.risk_band_high:
            return "HIGH"
        return "CRITICAL"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings instance.
    Called once at startup; reused everywhere via dependency injection.
    """
    return Settings()

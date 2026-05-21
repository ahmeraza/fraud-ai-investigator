"""
app/core/config.py
──────────────────
Application settings — updated for crypto monitoring.

New settings added:
  etherscan_api_key           — Etherscan V2 API key (free at etherscan.io)
  crypto_mixer_score_threshold — minimum score to trigger crypto alert (default 60)
  crypto_high_value_eth       — ETH amount considered high-value (default 10 ETH)
  eth_to_aed_rate             — approximate ETH/AED rate for reporting
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="        .env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env    : str = Field(default="development")
    app_version: str = Field(default="0.1.0")
    log_level  : str = Field(default="INFO")

    # ── LLM keys ─────────────────────────────────────────────────────────────
    gemini_api_key: str = Field(default="")
    groq_api_key  : str = Field(default="")
    gemini_model  : str = Field(default="gemini-2.5-flash-preview-05-20")
    groq_model    : str = Field(default="llama3-70b-8192")

    # ── Crypto monitoring ─────────────────────────────────────────────────────
    etherscan_api_key           : str   = Field(default="", description="Etherscan V2 API key — free at etherscan.io/myapikey")
    crypto_mixer_score_threshold: int   = Field(default=60,      description="Min score to trigger crypto alert (0-100)")
    crypto_high_value_eth       : float = Field(default=10.0,    description="ETH amount considered high-value")
    eth_to_aed_rate             : float = Field(default=12_000.0,description="Approximate ETH/AED conversion rate")

    # ── Payment thresholds ────────────────────────────────────────────────────
    high_value_threshold_aed     : float = Field(default=40_000.0)
    critical_amount_threshold_aed: float = Field(default=200_000.0)

    # ── OFAC sanctions screening ──────────────────────────────────────────────
    ofac_score_threshold: int = Field(default=75)

    # ── Risk bands ────────────────────────────────────────────────────────────
    risk_band_low   : int = Field(default=30)
    risk_band_medium: int = Field(default=70)
    risk_band_high  : int = Field(default=90)

    # ── FATF 2024 high-risk jurisdictions ─────────────────────────────────────
    high_risk_countries: list[str] = Field(
        default=[
            "KP", "IR", "MM",
            "SY", "YE", "SD", "PK",
            "PH", "HT", "LA", "NG",
            "TZ", "CM", "CD", "AO",
            "CU", "VE", "BY", "LY", "RU",
        ]
    )

    @property
    def is_development(self) -> bool: return self.app_env == "development"
    @property
    def has_gemini_key(self) -> bool: return bool(self.gemini_api_key)
    @property
    def has_groq_key(self) -> bool:   return bool(self.groq_api_key)
    @property
    def has_etherscan_key(self) -> bool: return bool(self.etherscan_api_key)

    def risk_band_label(self, score: int) -> str:
        if score < self.risk_band_low:    return "LOW"
        if score < self.risk_band_medium: return "MEDIUM"
        if score < self.risk_band_high:   return "HIGH"
        return "CRITICAL"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

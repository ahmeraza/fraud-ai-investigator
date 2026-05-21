"""
app/core/config.py
──────────────────
Application settings — updated for OFAC Priority 1.

Changes:
  - high_risk_countries expanded to full 2024 FATF grey + black list (20 countries)
  - ofac_score_threshold added (default 75 — tunes fuzzy match sensitivity)
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

    # ── Thresholds ────────────────────────────────────────────────────────────
    high_value_threshold_aed     : float = Field(default=40_000.0)
    critical_amount_threshold_aed: float = Field(default=200_000.0)

    # ── Risk bands ────────────────────────────────────────────────────────────
    risk_band_low   : int = Field(default=30)
    risk_band_medium: int = Field(default=70)
    risk_band_high  : int = Field(default=90)

    # ── OFAC sanctions screening ──────────────────────────────────────────────
    ofac_score_threshold: int = Field(
        default=75,
        description=(
            "Minimum fuzzy match score to trigger OFAC alert. "
            "75 = STRONG match. Lower = more alerts, higher FP rate."
        ),
    )

    # ── FATF 2024 high-risk jurisdictions ─────────────────────────────────────
    # Black list (FATF Call for Action — highest risk):
    #   KP North Korea, IR Iran, MM Myanmar
    # Grey list (Enhanced Monitoring):
    #   SY Syria, YE Yemen, SD Sudan, PK Pakistan, PH Philippines,
    #   HT Haiti, LA Laos, NG Nigeria, TZ Tanzania, CM Cameroon,
    #   CD DRC, AO Angola
    # Additional OFAC/UN sanctioned:
    #   CU Cuba, VE Venezuela, BY Belarus, LY Libya, RU Russia
    high_risk_countries: list[str] = Field(
        default=[
            "KP", "IR", "MM",           # FATF black list
            "SY", "YE", "SD", "PK",     # FATF grey list (MENA-adjacent)
            "PH", "HT", "LA", "NG",     # FATF grey list (other)
            "TZ", "CM", "CD", "AO",     # FATF grey list (Africa)
            "CU", "VE", "BY", "LY",     # OFAC/UN additional
            "RU",                        # Russia sanctions (2022+)
        ]
    )

    @property
    def is_development(self) -> bool: return self.app_env == "development"
    @property
    def has_gemini_key(self) -> bool: return bool(self.gemini_api_key)
    @property
    def has_groq_key(self) -> bool:   return bool(self.groq_api_key)

    def risk_band_label(self, score: int) -> str:
        if score < self.risk_band_low:    return "LOW"
        if score < self.risk_band_medium: return "MEDIUM"
        if score < self.risk_band_high:   return "HIGH"
        return "CRITICAL"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

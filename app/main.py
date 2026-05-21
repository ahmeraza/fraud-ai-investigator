"""
app/main.py
────────────
FastAPI application entry point — updated for crypto monitoring.

New in this version: crypto router registered at /v1/crypto
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.shared.models import HealthResponse

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    s = get_settings()
    logger.info(f"Starting Fraud AI Investigator v{s.app_version}")
    logger.info(f"Environment   : {s.app_env}")
    logger.info(f"Gemini key    : {s.has_gemini_key}")
    logger.info(f"Groq key      : {s.has_groq_key}")
    logger.info(f"Etherscan key : {s.has_etherscan_key}")
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    s = get_settings()

    app = FastAPI(
        title       = "Fraud AI Investigator",
        description = (
            "Agentic AI fraud analysis — payment AML, crypto mixer detection, "
            "multi-agent orchestration, HITL governance. UAE/MENA fintech."
        ),
        version  = s.app_version,
        lifespan = lifespan,
        docs_url = "/docs",
        redoc_url= "/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"] if s.is_development else ["http://localhost:8501"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    from app.api.alerts import router as alerts_router
    from app.api.triage import router as triage_router
    from app.api.crypto import router as crypto_router

    app.include_router(alerts_router, prefix="/v1/alerts", tags=["Alerts"])
    app.include_router(triage_router, prefix="/v1/triage", tags=["Triage"])
    app.include_router(crypto_router, prefix="/v1/crypto", tags=["Crypto Monitoring"])

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root():
    return {"service": "Fraud AI Investigator", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse, tags=["System"])
def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status      = "ok",
        version     = s.app_version,
        environment = s.app_env,
        llm_providers = {
            "gemini"    : s.has_gemini_key,
            "groq"      : s.has_groq_key,
            "etherscan" : s.has_etherscan_key,
        },
    )

"""
app/main.py
────────────
FastAPI application entry point — updated for Week 2.

Changes from Week 1:
  - Alerts router registered at /v1/alerts
  - Startup log now shows alert store stats

Run with:
    uv run uvicorn app.main:app --reload

Then open: http://localhost:8000/docs
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
    settings = get_settings()
    logger.info(f"Starting Fraud AI Investigator v{settings.app_version}")
    logger.info(f"Environment: {settings.app_env}")
    logger.info(f"Gemini key present: {settings.has_gemini_key}")
    logger.info(f"Groq key present: {settings.has_groq_key}")
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Fraud AI Investigator",
        description=(
            "Agentic AI system for end-to-end fraud analysis. "
            "Multi-agent orchestration, explainable risk scoring, "
            "and human-in-the-loop governance for MENA fintech platforms."
        ),
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_development else ["http://localhost:8501"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    from app.api.alerts import router as alerts_router
    app.include_router(alerts_router, prefix="/v1/alerts", tags=["Alerts"])

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root():
    return {"service": "Fraud AI Investigator", "docs": "/docs", "health": "/health"}


@app.get("/health", response_model=HealthResponse, tags=["System"])
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
        llm_providers={
            "gemini": settings.has_gemini_key,
            "groq": settings.has_groq_key,
        },
    )

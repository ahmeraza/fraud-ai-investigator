"""
FastAPI application entry point — Phase 7 update.
Adds HTML landing page at root for Hugging Face Spaces.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

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
            "Agentic AI fraud analysis — payment AML, crypto monitoring, "
            "LangGraph multi-agent investigation, HITL governance. UAE/MENA."
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

    from app.api.alerts        import router as alerts_router
    from app.api.triage        import router as triage_router
    from app.api.crypto        import router as crypto_router
    from app.api.investigation import router as investigation_router
    from app.api.hitl          import router as hitl_router

    app.include_router(alerts_router,        prefix="/v1/alerts",      tags=["Alerts"])
    app.include_router(triage_router,        prefix="/v1/triage",      tags=["Triage"])
    app.include_router(crypto_router,        prefix="/v1/crypto",      tags=["Crypto Monitoring"])
    app.include_router(investigation_router, prefix="/v1/investigate", tags=["Investigation"])
    app.include_router(hitl_router,          prefix="/v1/hitl",        tags=["HITL Review"])

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse("""
    <html>
    <head>
        <title>Fraud AI Investigator</title>
        <style>
            body { font-family: system-ui; max-width: 700px; margin: 60px auto; padding: 0 20px; }
            h1 { color: #1a1a2e; }
            .badge { background: #4CAF50; color: white; padding: 4px 10px; border-radius: 12px; font-size: 13px; }
            a { color: #1565C0; text-decoration: none; font-weight: 500; }
            .card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; margin: 12px 0; }
            .tag { background: #f0f4ff; color: #1565C0; padding: 3px 8px; border-radius: 4px; font-size: 12px; margin: 2px; display: inline-block; }
        </style>
    </head>
    <body>
        <h1>🔍 Fraud AI Investigator <span class="badge">Running</span></h1>
        <p>Agentic AI fraud investigation for UAE/MENA fintech — LangGraph multi-agent pipeline with HITL governance.</p>

        <div class="card">
            <b>📖 API Documentation</b><br>
            <a href="/docs">Interactive Swagger UI →</a> &nbsp;|&nbsp; <a href="/redoc">ReDoc →</a>
        </div>

        <div class="card">
            <b>🔗 Key endpoints</b><br><br>
            <a href="/health">GET /health</a> — System status<br>
            <a href="/v1/alerts">GET /v1/alerts</a> — List alerts<br>
            <a href="/docs#/Investigation">POST /v1/investigate/{id}</a> — LangGraph investigation<br>
            <a href="/docs#/HITL%20Review">GET /v1/hitl/queue</a> — Analyst review queue
        </div>

        <div class="card">
            <b>🛠 Tech stack</b><br><br>
            <span class="tag">FastAPI</span>
            <span class="tag">LangGraph</span>
            <span class="tag">Gemini 2.5 Flash</span>
            <span class="tag">Groq / Llama 3</span>
            <span class="tag">OFAC SDN</span>
            <span class="tag">Etherscan V2</span>
            <span class="tag">Docker</span>
            <span class="tag">Pydantic v2</span>
        </div>

        <div class="card">
            <b>📊 Pipeline</b><br>
            <small>Alert Engine → LLM Triage → LangGraph Investigation (5 agents) → HITL Review → Fraud Memory → Audit Trail</small>
        </div>

        <p><small>
            <a href="https://github.com/ahmeraza/fraud-ai-investigator">GitHub →</a> &nbsp;|&nbsp;
            Cost: $0/month (all free tiers)
        </small></p>
    </body>
    </html>
    """)


@app.get("/health", response_model=HealthResponse, tags=["System"])
def health() -> HealthResponse:
    s = get_settings()
    return HealthResponse(
        status      = "ok",
        version     = s.app_version,
        environment = s.app_env,
        llm_providers = {
            "gemini"   : s.has_gemini_key,
            "groq"     : s.has_groq_key,
            "etherscan": s.has_etherscan_key,
        },
    )
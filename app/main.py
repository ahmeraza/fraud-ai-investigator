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
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fraud AI Investigator</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;color:#1a1a1a;background:#fff;line-height:1.6}
.hero{padding:3rem 2rem 2rem;text-align:center;border-bottom:1px solid #f0f0f0}
.badge-row{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:1.5rem}
.badge{font-size:11px;padding:3px 10px;border-radius:20px;border:1px solid #e0e0e0;color:#666;background:#f8f8f8}
.badge.live{background:#f0faf4;color:#1a7a4a;border-color:#b8e8cc}
h1{font-size:2rem;font-weight:600;margin-bottom:.75rem}
.hero-sub{font-size:1rem;color:#555;max-width:540px;margin:0 auto 1.5rem}
.btn-row{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
a.btn{padding:9px 20px;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;text-decoration:none;border:1px solid #ddd;color:#1a1a1a;background:#fff;display:inline-flex;align-items:center;gap:6px}
a.btn-primary{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.section{padding:2rem;max-width:900px;margin:0 auto}
.section-label{font-size:11px;font-weight:600;color:#999;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.5rem}
.section-title{font-size:1.3rem;font-weight:600;margin-bottom:.4rem}
.section-sub{font-size:14px;color:#666;margin-bottom:1.5rem}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:1.5rem}
.stat{background:#f8f8f8;border-radius:8px;padding:.875rem;text-align:center}
.stat-num{font-size:1.5rem;font-weight:600}
.stat-label{font-size:11px;color:#888;margin-top:2px}
.pipeline{background:#f8f8f8;border-radius:10px;padding:1.25rem;margin-bottom:1.5rem}
.pipe-steps{display:flex;align-items:center;flex-wrap:wrap;gap:6px}
.pipe-label{font-size:12px;font-weight:500;padding:5px 10px;border-radius:6px;border:1px solid #e0e0e0;background:#fff;white-space:nowrap}
.pipe-arrow{color:#bbb;font-size:14px}
.features{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
.feat{background:#fff;border:1px solid #f0f0f0;border-radius:12px;padding:1.1rem}
.feat-icon{width:38px;height:38px;border-radius:8px;display:flex;align-items:center;justify-content:center;margin-bottom:10px;font-size:1.1rem}
.feat h3{font-size:14px;font-weight:600;margin-bottom:4px}
.feat p{font-size:12px;color:#666;line-height:1.5}
.endpoints{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ep{background:#f8f8f8;border-radius:8px;padding:10px 12px;display:flex;align-items:flex-start;gap:10px}
.method{font-size:10px;font-weight:600;padding:2px 6px;border-radius:4px;min-width:34px;text-align:center;margin-top:2px}
.method.post{background:#f0faf4;color:#1a7a4a}
.method.get{background:#e8f0fe;color:#1a56db}
.ep-path{font-size:12px;font-weight:600;font-family:monospace;margin-bottom:2px}
.ep-desc{font-size:11px;color:#888}
hr{border:none;border-top:1px solid #f0f0f0}
.footer{padding:1.25rem 2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;max-width:900px;margin:0 auto}
.footer-links{display:flex;gap:16px}
.footer a{font-size:13px;color:#888;text-decoration:none}
.footer a:hover{color:#1a1a1a}
</style>
</head>
<body>

<div class="hero">
  <div class="badge-row">
    <span class="badge live">&#9679; Live on Hugging Face</span>
    <span class="badge">269 tests passing</span>
    <span class="badge">$0/month</span>
    <span class="badge">UAE/MENA AML</span>
  </div>
  <h1>&#128269; Fraud AI Investigator</h1>
  <p class="hero-sub">An agentic AI system that investigates payment fraud end-to-end — from the first rule firing to the analyst's final verdict — built for digital financial platforms in the UAE and MENA region.</p>
  <div class="btn-row">
    <a href="/docs" class="btn btn-primary">&#128196; API Documentation</a>
    <a href="https://github.com/ahmeraza/fraud-ai-investigator" class="btn">&#128279; GitHub</a>
    <a href="/health" class="btn">&#10084; Health check</a>
  </div>
</div>

<div class="section">
  <div class="stats">
    <div class="stat"><div class="stat-num">5</div><div class="stat-label">fraud rules</div></div>
    <div class="stat"><div class="stat-num">12k</div><div class="stat-label">OFAC entities</div></div>
    <div class="stat"><div class="stat-num">590k</div><div class="stat-label">real transactions</div></div>
    <div class="stat"><div class="stat-num">269</div><div class="stat-label">tests passing</div></div>
  </div>

  <div class="pipeline">
    <p style="font-size:12px;color:#888;margin-bottom:10px;font-weight:600;">INVESTIGATION PIPELINE</p>
    <div class="pipe-steps">
      <span class="pipe-label">&#128276; Alert engine</span>
      <span class="pipe-arrow">&#8594;</span>
      <span class="pipe-label">&#129504; LLM triage</span>
      <span class="pipe-arrow">&#8594;</span>
      <span class="pipe-label">&#128296; 5 parallel agents</span>
      <span class="pipe-arrow">&#8594;</span>
      <span class="pipe-label">&#128100; HITL review</span>
      <span class="pipe-arrow">&#8594;</span>
      <span class="pipe-label">&#128202; Fraud memory</span>
    </div>
  </div>
</div>

<hr>

<div class="section">
  <p class="section-label">Capabilities</p>
  <p class="section-title">Six modules, one pipeline</p>
  <p class="section-sub">Each module runs independently and feeds the next — from millisecond rule evaluation to multi-minute LangGraph investigation.</p>

  <div class="features">
    <div class="feat">
      <div class="feat-icon" style="background:#e8f0fe">&#128276;</div>
      <h3>Alert engine</h3>
      <p>Five deterministic rules — high value (AED 40k), FATF corridor, device mismatch, new account, OFAC name match — fire instantly on every transaction.</p>
    </div>
    <div class="feat">
      <div class="feat-icon" style="background:#f0ebfe">&#129504;</div>
      <h3>LLM triage</h3>
      <p>Gemini 2.5 Flash scores each alert 0–100 with a UAE regulatory narrative. Groq Llama 3 activates automatically on rate limits.</p>
    </div>
    <div class="feat">
      <div class="feat-icon" style="background:#fff8e8">&#128737;</div>
      <h3>Sanctions screening</h3>
      <p>Real OFAC SDN list — 12,000 entities with Arabic fuzzy matching for Mohammed/Muhammad/Mohammad name variants across FATF 2024 high-risk jurisdictions.</p>
    </div>
    <div class="feat">
      <div class="feat-icon" style="background:#e8f8f0">&#8383;</div>
      <h3>Crypto monitoring</h3>
      <p>Etherscan V2 on-chain screening. Detects Tornado Cash, Blender, Sinbad interactions. Behavioural mixer patterns for VARA Travel Rule compliance.</p>
    </div>
    <div class="feat">
      <div class="feat-icon" style="background:#fee8e8">&#128296;</div>
      <h3>LangGraph investigation</h3>
      <p>Five specialist agents run in parallel — transaction, KYC, sanctions, crypto, synthesis — with MemorySaver checkpointing for HITL graph resumption.</p>
    </div>
    <div class="feat">
      <div class="feat-icon" style="background:#f0faf4">&#128100;</div>
      <h3>HITL governance</h3>
      <p>Analyst queue with investigation summary, similar past cases, and regulatory guidance. Confirmed fraud auto-flags STR obligation per CBUAE AML/CFT.</p>
    </div>
  </div>
</div>

<hr>

<div class="section">
  <p class="section-label">API reference</p>
  <p class="section-title">Key endpoints</p>
  <p class="section-sub">Full interactive docs at <a href="/docs" style="color:#1a56db">/docs</a> — try any endpoint directly in the browser.</p>

  <div class="endpoints">
    <div class="ep"><span class="method post">POST</span><div><div class="ep-path">/v1/alerts/generate</div><div class="ep-desc">Run alert engine on transactions</div></div></div>
    <div class="ep"><span class="method post">POST</span><div><div class="ep-path">/v1/triage/batch</div><div class="ep-desc">LLM score all pending alerts</div></div></div>
    <div class="ep"><span class="method post">POST</span><div><div class="ep-path">/v1/investigate/batch</div><div class="ep-desc">5-agent LangGraph investigation</div></div></div>
    <div class="ep"><span class="method post">POST</span><div><div class="ep-path">/v1/crypto/screen</div><div class="ep-desc">On-chain mixer detection</div></div></div>
    <div class="ep"><span class="method get">GET</span><div><div class="ep-path">/v1/hitl/queue</div><div class="ep-desc">Analyst review queue</div></div></div>
    <div class="ep"><span class="method post">POST</span><div><div class="ep-path">/v1/hitl/{id}/decision</div><div class="ep-desc">Submit analyst verdict</div></div></div>
    <div class="ep"><span class="method get">GET</span><div><div class="ep-path">/v1/alerts/{id}/audit</div><div class="ep-desc">Full immutable case timeline</div></div></div>
    <div class="ep"><span class="method get">GET</span><div><div class="ep-path">/health</div><div class="ep-desc">System status and key check</div></div></div>
  </div>
</div>

<hr>

<div class="footer">
  <div class="footer-links">
    <a href="/docs">API docs</a>
    <a href="/redoc">ReDoc</a>
    <a href="https://github.com/ahmeraza/fraud-ai-investigator">GitHub</a>
    <a href="/health">Health</a>
  </div>
  <span style="font-size:13px;color:#bbb">Built by Ahmed Raza &middot; $0/month &middot; all free tiers</span>
</div>

</body>
</html>""")

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
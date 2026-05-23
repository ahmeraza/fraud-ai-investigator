"""
app/main.py
────────────
FastAPI application entry point — Phase 7 final.
Interactive HTML landing page at root. Option 1: wide layout (1100px).
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.shared.models import HealthResponse

logger = get_logger(__name__)

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fraud AI Investigator</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;color:#1a1a1a;background:#fff}
.hero{padding:3.5rem 2rem 2.5rem;text-align:center;border-bottom:1px solid #f0f0f0}
.hero-title{font-size:2.4rem;font-weight:700;margin-bottom:1rem}
.rotating-tags{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-bottom:1.25rem;min-height:28px;align-items:center}
.tag{font-size:12px;padding:4px 12px;border-radius:20px;border:1px solid;opacity:0;animation:fadeTag 0.5s ease forwards}
.tag.blue{background:#e8f0fe;color:#1a56db;border-color:#c5d8fc}
.tag.green{background:#f0faf4;color:#1a7a4a;border-color:#b8e8cc}
.tag.amber{background:#fff8e8;color:#8a5a00;border-color:#f0d590}
.tag.red{background:#fee8e8;color:#9a2a2a;border-color:#f5b8b8}
.tag.purple{background:#f0ebfe;color:#5a35c0;border-color:#c8b8f8}
@keyframes fadeTag{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.hero-desc{font-size:1rem;color:#555;max-width:900px;margin:0 auto 1.5rem;line-height:1.6}
.btn-row{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
a.btn{padding:10px 22px;border-radius:8px;font-size:14px;font-weight:500;text-decoration:none;border:1px solid #ddd;color:#1a1a1a;background:#fff;display:inline-flex;align-items:center;gap:6px;transition:all 0.2s}
a.btn:hover{background:#f5f5f5;border-color:#ccc}
a.btn-primary{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
a.btn-primary:hover{background:#333}
.wrap{max-width:1100px;margin:0 auto;padding:0 2rem}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:1.5rem 2rem;max-width:1100px;margin:0 auto}
.stat{background:#f8f8f8;border-radius:10px;padding:1rem;text-align:center}
.stat-num{font-size:1.6rem;font-weight:700;color:#1a1a1a}
.stat-label{font-size:11px;color:#888;margin-top:2px}
.section{padding:1.5rem 2rem;max-width:1100px;margin:0 auto}
.section-label{font-size:11px;font-weight:600;color:#999;letter-spacing:.08em;text-transform:uppercase;margin-bottom:.4rem}
.section-title{font-size:1.2rem;font-weight:600;margin-bottom:.3rem}
.section-sub{font-size:13px;color:#666;margin-bottom:1.2rem}
.pipe-wrapper{background:#f8f8f8;border-radius:12px;padding:1.1rem 1.25rem;overflow:hidden}
.pipe-track{display:flex;align-items:center;gap:0;width:max-content;animation:scroll 10s linear infinite}
.pipe-track:hover{animation-play-state:paused}
.pipe-item{display:flex;align-items:center;gap:0;white-space:nowrap}
.pipe-label{font-size:13px;font-weight:500;padding:8px 16px;border-radius:8px;background:#fff;border:1px solid #e8e8e8;white-space:nowrap;transition:all 0.3s;margin:0 4px}
.pipe-label.active{border-color:#1a56db;color:#1a56db;background:#e8f0fe}
.pipe-arrow{color:#bbb;font-size:18px;font-weight:300;margin:0 2px}
@keyframes scroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.cards-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.flip-card{height:180px;cursor:pointer;perspective:1000px}
.flip-inner{position:relative;width:100%;height:100%;transition:transform 0.55s cubic-bezier(.4,0,.2,1);transform-style:preserve-3d}
.flip-card.flipped .flip-inner{transform:rotateY(180deg)}
.flip-front,.flip-back{position:absolute;width:100%;height:100%;backface-visibility:hidden;-webkit-backface-visibility:hidden;border-radius:12px;border:1px solid #f0f0f0}
.flip-front{background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:1rem}
.flip-back{background:#1a1a1a;color:#fff;transform:rotateY(180deg);display:flex;flex-direction:column;justify-content:center;padding:1.1rem;border-color:#333}
.feat-emoji{font-size:2rem;line-height:1}
.feat-title{font-size:14px;font-weight:600;text-align:center;color:#1a1a1a}
.feat-hint{font-size:10px;color:#bbb;margin-top:2px}
.flip-back h4{font-size:13px;font-weight:600;margin-bottom:6px;color:#fff}
.flip-back p{font-size:11.5px;color:#ccc;line-height:1.55}
.flip-back .tag-mini{display:inline-block;font-size:10px;padding:2px 7px;border-radius:10px;background:#333;color:#aaa;margin-top:8px;margin-right:3px}
.ep-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.ep{background:#f8f8f8;border-radius:8px;padding:10px 12px;display:flex;align-items:flex-start;gap:10px;text-decoration:none;border:1px solid transparent;transition:all 0.2s}
.ep:hover{background:#fff;border-color:#e0e0e0;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.method{font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px;min-width:36px;text-align:center;margin-top:1px;flex-shrink:0}
.method.post{background:#f0faf4;color:#1a7a4a}
.method.get{background:#e8f0fe;color:#1a56db}
.ep-path{font-size:12px;font-weight:600;font-family:monospace;margin-bottom:2px;color:#1a1a1a}
.ep-desc{font-size:11px;color:#888}
hr{border:none;border-top:1px solid #f0f0f0}
.footer{padding:1.25rem 2rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;max-width:1100px;margin:0 auto}
.footer-links{display:flex;gap:16px}
.footer a{font-size:13px;color:#888;text-decoration:none}
.footer a:hover{color:#1a1a1a}
</style>
</head>
<body>

<div class="hero">
  <div class="rotating-tags" id="tagRow"></div>
  <h1 class="hero-title">&#128269; Fraud AI Investigator</h1>
  <p class="hero-desc">Agentic AI fraud investigation for UAE/MENA fintech &mdash; LangGraph multi-agent pipeline with OFAC sanctions screening, on-chain crypto monitoring, KYC compliance, and HITL governance.</p>
  <div class="btn-row">
    <a href="/docs" class="btn btn-primary">&#128196; Live API Docs</a>
    <a href="https://github.com/ahmeraza/fraud-ai-investigator" class="btn">&#128279; GitHub</a>
    <a href="/health" class="btn">&#10084; Health check</a>
  </div>
</div>

<div class="stats">
  <div class="stat-num">17</div><div class="stat-label">compliance rules</div>
  <div class="stat"><div class="stat-num">12k</div><div class="stat-label">OFAC entities</div></div>
  <div class="stat"><div class="stat-num">590k</div><div class="stat-label">real transactions</div></div>
  <div class="stat"><div class="stat-num">298</div><div class="stat-label">tests passing</div></div>
</div>

<hr>

<div class="section">
  <p class="section-label">How it works</p>
  <p class="section-title">Investigation pipeline</p>
  <p class="section-sub">Each stage feeds into the next automatically &mdash; hover to pause.</p>
  <div class="pipe-wrapper">
    <div class="pipe-track" id="pipeTrack"></div>
  </div>
</div>

<hr>

<div class="section">
  <p class="section-label">Capabilities</p>
  <p class="section-title">Six modules, one pipeline</p>
  <p class="section-sub">Click any card to see what it does.</p>
  <div class="cards-grid" id="cardsGrid"></div>
</div>

<hr>

<div class="section">
  <p class="section-label">API reference</p>
  <p class="section-title">Key endpoints</p>
  <p class="section-sub">Click any endpoint to open the interactive docs. Full reference at <a href="/docs" style="color:#1a56db">/docs</a></p>
  <div class="ep-grid" id="epGrid"></div>
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

<script>
const tags=[
  {text:"Fintech AML",cls:"blue"},{text:"Sanctions Screening",cls:"amber"},
  {text:"On-chain Crypto",cls:"green"},{text:"KYC Compliance",cls:"purple"},
  {text:"LangGraph Agents",cls:"red"},{text:"HITL Governance",cls:"blue"},
  {text:"OFAC SDN",cls:"amber"},{text:"UAE/MENA",cls:"green"},
  {text:"269 Tests &#10003;",cls:"blue"},{text:"FATF 2024",cls:"red"},
  {text:"$0/month",cls:"purple"},{text:"VARA Compliance",cls:"green"}
];
const tagRow=document.getElementById('tagRow');
let ti=0;
function showNextTags(){
  tagRow.innerHTML='';
  for(let i=0;i<5;i++){
    const t=tags[(ti+i)%tags.length];
    const el=document.createElement('span');
    el.className='tag '+t.cls;
    el.innerHTML=t.text;
    el.style.animationDelay=(i*0.08)+'s';
    tagRow.appendChild(el);
  }
  ti=(ti+5)%tags.length;
}
showNextTags();
setInterval(showNextTags,2800);

const pipeSteps=[
  {label:"&#128276; Alert engine",cls:"active"},
  {label:"&#129504; LLM triage",cls:""},
  {label:"&#9889; 5 parallel agents",cls:""},
  {label:"&#128100; HITL review",cls:""},
  {label:"&#128202; Fraud memory",cls:""}
];
function buildPipe(){
  const track=document.getElementById('pipeTrack');
  const chunk=()=>pipeSteps.map((s,i)=>
    '<span class="pipe-item"><span class="pipe-label '+s.cls+'">'+s.label+'</span>'+(i<pipeSteps.length-1?'<span class="pipe-arrow">&#8594;</span>':'')+'</span>'
  ).join('');
  track.innerHTML=chunk()+chunk();
}
buildPipe();
let activeStep=0;
setInterval(()=>{
  activeStep=(activeStep+1)%pipeSteps.length;
  document.querySelectorAll('.pipe-label').forEach((el,i)=>{
    el.classList.toggle('active',i%pipeSteps.length===activeStep);
  });
},1200);

const features=[
  {emoji:"&#128276;",title:"Alert engine",hint:"Click to learn more",
   back:"Five deterministic rules fire instantly &mdash; high value (AED 40k CBUAE threshold), FATF corridor (20 jurisdictions), device mismatch, new account pattern, OFAC name match.",
   tags:["AML","FATF","OFAC"]},
  {emoji:"&#129504;",title:"LLM triage",hint:"Click to learn more",
   back:"Gemini 2.5 Flash scores each alert 0&ndash;100 with a UAE regulatory narrative. Auto-falls back to Groq Llama 3 on rate limits. Structured JSON output validated by Pydantic.",
   tags:["Gemini","Groq","LLM"]},
  {emoji:"&#128737;",title:"Sanctions screening",hint:"Click to learn more",
   back:"Real OFAC SDN list &mdash; 12,000 entities. Arabic fuzzy matching handles Mohammed/Muhammad/Mohammad variants. Covers FATF 2024 grey and black list countries.",
   tags:["OFAC SDN","KYC","Fuzzy match"]},
  {emoji:"&#8383;",title:"Crypto monitoring",hint:"Click to learn more",
   back:"Etherscan V2 on-chain screening. Detects Tornado Cash, Blender, Sinbad interactions. Behavioural mixer patterns &mdash; round amounts, rapid in/out. VARA Travel Rule compliance.",
   tags:["On-chain","VARA","Mixer"]},
  {emoji:"&#9889;",title:"LangGraph agents",hint:"Click to learn more",
   back:"Five specialist agents run in parallel &mdash; transaction, KYC, sanctions, crypto, synthesis. MemorySaver checkpointing enables HITL interrupts and graph replay.",
   tags:["LangGraph","Parallel","Agents"]},
  {emoji:"&#128100;",title:"HITL governance",hint:"Click to learn more",
   back:"Analyst queue with investigation summary, similar past cases, and regulatory guidance. CONFIRMED_FRAUD auto-flags STR obligation per CBUAE AML/CFT within 2 working days.",
   tags:["HITL","STR","Audit"]}
];
const grid=document.getElementById('cardsGrid');
features.forEach(f=>{
  const card=document.createElement('div');
  card.className='flip-card';
  card.innerHTML='<div class="flip-inner"><div class="flip-front"><div class="feat-emoji">'+f.emoji+'</div><div class="feat-title">'+f.title+'</div><div class="feat-hint">'+f.hint+'</div></div><div class="flip-back"><h4>'+f.title+'</h4><p>'+f.back+'</p><div>'+f.tags.map(t=>'<span class="tag-mini">'+t+'</span>').join('')+'</div></div></div>';
  card.addEventListener('click',()=>card.classList.toggle('flipped'));
  grid.appendChild(card);
});

const endpoints=[
  {method:"POST",path:"/v1/alerts/generate",desc:"Run alert engine on transactions",anchor:"alerts"},
  {method:"POST",path:"/v1/triage/batch",desc:"LLM score all pending alerts",anchor:"triage"},
  {method:"POST",path:"/v1/investigate/batch",desc:"5-agent LangGraph investigation",anchor:"investigation"},
  {method:"POST",path:"/v1/crypto/screen",desc:"On-chain mixer detection",anchor:"crypto-monitoring"},
  {method:"GET",path:"/v1/hitl/queue",desc:"Analyst review queue",anchor:"hitl-review"},
  {method:"POST",path:"/v1/hitl/{id}/decision",desc:"Submit analyst verdict",anchor:"hitl-review"},
  {method:"GET",path:"/v1/alerts/{id}/audit",desc:"Full immutable case timeline",anchor:"alerts"},
  {method:"GET",path:"/health",desc:"System status and key check",anchor:"system"}
];
const epGrid=document.getElementById('epGrid');
endpoints.forEach(ep=>{
  const div=document.createElement('a');
  div.className='ep';
  div.href='/docs#/'+ep.anchor;
  div.innerHTML='<span class="method '+ep.method.toLowerCase()+'">'+ep.method+'</span><div><div class="ep-path">'+ep.path+'</div><div class="ep-desc">'+ep.desc+'</div></div>';
  epGrid.appendChild(div);
});
</script>
</body>
</html>"""


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
    from app.api.compliance import router as compliance_router

    app.include_router(alerts_router,        prefix="/v1/alerts",      tags=["Alerts"])
    app.include_router(triage_router,        prefix="/v1/triage",      tags=["Triage"])
    app.include_router(crypto_router,        prefix="/v1/crypto",      tags=["Crypto Monitoring"])
    app.include_router(investigation_router, prefix="/v1/investigate", tags=["Investigation"])
    app.include_router(hitl_router,          prefix="/v1/hitl",        tags=["HITL Review"])
    app.include_router(compliance_router, prefix="/v1/compliance", tags=["Compliance Engine"])

    return app


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse(LANDING_HTML)


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

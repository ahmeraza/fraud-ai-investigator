# Fraud AI Investigator

> An agentic AI system that automates end-to-end fraud analysis using multi-agent orchestration, explainable risk scoring, and human-in-the-loop governance — built for digital financial platforms operating in the UAE/MENA region.

---

## Architecture overview

```
Transaction Alert
       │
       ▼
┌─────────────────┐
│  Alert Engine   │  ← Deterministic rules (fast, cheap)
│  (Rule-based)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Triage Agent   │  ← LLM: severity scoring + narrative
└────────┬────────┘
         │
    ┌────┴─────┐
    ▼    ▼     ▼
 [TX] [KYC] [Sanctions]  ← Parallel specialist agents (LangGraph)
    └────┬─────┘
         ▼
┌─────────────────┐
│  Risk Synthesis │  ← Score 0–100 + case narrative
└────────┬────────┘
         ▼
┌─────────────────┐
│  HITL Review    │  ← Analyst approves / closes
└────────┬────────┘
         ▼
┌─────────────────┐
│  Audit Trail    │  ← Full immutable case timeline
└─────────────────┘
```

## Tech stack

| Layer | Technology |
|---|---|
| API backend | FastAPI + Pydantic v2 |
| Agent orchestration | LangGraph |
| Primary LLM | Gemini 2.5 Flash (free tier) |
| Fallback LLM | Groq / Llama 3 (free tier) |
| Dashboard | Streamlit |
| Hosting | Hugging Face Spaces (Docker, free) |
| Package manager | uv |
| Testing | pytest + pytest-cov |

**Cost to run: $0/month** — all free tiers, no credit card required.

---

## Quickstart

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/fraud-ai-investigator.git
cd fraud-ai-investigator
```

### 2. Install uv (fast Python package manager)

```bash
# Mac/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. Install dependencies

```bash
uv venv
uv sync
```

### 4. Set up environment variables

```bash
cp .env.example .env
# Edit .env and add your free API keys:
#   GEMINI_API_KEY → https://aistudio.google.com/apikey
#   GROQ_API_KEY   → https://console.groq.com/keys
```

### 5. Generate the synthetic dataset

```bash
uv run python scripts/generate_data.py
```

### 6. Start the API

```bash
uv run uvicorn app.main:app --reload
# Open: http://localhost:8000/docs
```

### 7. Run tests

```bash
uv run pytest tests/ -v --cov=app
```

---

## Project structure

```
fraud-ai-investigator/
├── app/
│   ├── api/            # FastAPI route definitions
│   ├── core/           # Config, logging, constants
│   ├── llm/            # Gemini + Groq client wrappers
│   ├── agents/         # Specialist fraud agents
│   ├── graph/          # LangGraph workflow definition
│   ├── services/       # Business logic (alert engine, triage, HITL)
│   ├── data/           # Synthetic JSON datasets
│   ├── shared/         # Pydantic models shared across layers
│   └── main.py         # FastAPI application entry point
├── dashboard/          # Streamlit UI
├── notebooks/          # Jupyter exploration notebooks
├── scripts/            # Utility scripts (data gen, etc.)
├── tests/              # pytest test suite
├── deploy/             # Docker + Hugging Face config
├── doc/Screenshots/    # UI screenshots for README
├── .env.example        # Environment variable template
├── pyproject.toml      # Dependencies (managed by uv)
├── Dockerfile          # Container definition
└── docker-compose.yml  # Local multi-service setup
```

---

## Development roadmap

| Phase | Description | Status |
|---|---|---|
| Week 1 | Repo setup, Python env, folder structure, synthetic dataset | ✅ Complete |
| Week 2 | Pydantic models, alert engine rules, FastAPI endpoints | 🔄 In progress |
| Week 3–4 | Triage service + LLM narrative generation | ⏳ Planned |
| Week 5–7 | LangGraph multi-agent investigation | ⏳ Planned |
| Week 8–9 | HITL review + fraud memory + audit trail | ⏳ Planned |
| Week 10–11 | Streamlit dashboard + Docker packaging | ⏳ Planned |
| Week 12 | Deploy to Hugging Face Spaces (live demo) | ⏳ Planned |

---

## MENA-specific features

- AED currency support with UAE Central Bank reporting thresholds (AED 40,000)
- FATF high-risk jurisdiction corridor scoring
- Arabic name transliteration-aware sanctions matching
- UAE/GCC country risk weighting in scoring model

---

## Contributing

This is a portfolio project. Issues and PRs welcome.

## License

MIT — see [LICENSE](LICENSE)

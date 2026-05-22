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
│  5 fraud rules  │     High-value, FATF corridor, device mismatch,
│  OFAC screening │     new account, OFAC name match
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM Triage     │  ← Gemini Flash: severity score + regulatory narrative
│  (Phase 3)      │
└────────┬────────┘
         │
    ┌────┴──────────────────────┐
    ▼          ▼               ▼
[TX Agent] [KYC Agent]  [Sanctions Agent]  ← Parallel LangGraph agents
    │    [Crypto Agent]        │            (Phase 4)
    └────────────┬─────────────┘
                 ▼
        ┌─────────────────┐
        │ Synthesis Agent │  ← LLM: composite score + investigation narrative
        │  + Fraud Memory │     includes similar past cases (Phase 5)
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │   HITL Review   │  ← Analyst verdict: CONFIRMED_FRAUD /
        │   (Phase 5)     │     FALSE_POSITIVE / ESCALATED
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │   Audit Trail   │  ← Full immutable case timeline
        │   + STR Flag    │     CBUAE STR obligation if confirmed
        └─────────────────┘
```

## Tech stack

| Layer | Technology |
|---|---|
| API backend | FastAPI + Pydantic v2 |
| Agent orchestration | LangGraph |
| Primary LLM | Gemini 2.5 Flash (free tier) |
| Fallback LLM | Groq / Llama 3 (free tier) |
| Crypto monitoring | Etherscan V2 API (free tier) |
| Sanctions screening | OFAC SDN real data (treasury.gov) |
| Transaction data | IEEE-CIS real dataset (Kaggle) |
| Dashboard | Streamlit |
| Hosting | Hugging Face Spaces (Docker, free) |
| Package manager | uv |
| Testing | pytest + pytest-cov |

**Cost to run: $0/month** — all free tiers, no credit card required.

---

## Quickstart

### 1. Clone the repo

```bash
git clone https://github.com/ahmeraza/fraud-ai-investigator.git
cd fraud-ai-investigator
```

### 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Install dependencies

```bash
uv venv && uv sync
uv pip install fastapi uvicorn pydantic pydantic-settings faker httpx pytest \
  pytest-cov pytest-dotenv langchain-core google-genai groq langgraph \
  rapidfuzz pandas numpy requests
```

### 4. Configure API keys

```bash
cp .env.example .env
# Add to .env:
#   GEMINI_API_KEY    → https://aistudio.google.com/apikey
#   GROQ_API_KEY      → https://console.groq.com/keys
#   ETHERSCAN_API_KEY → https://etherscan.io/myapikey  (optional)
```

### 5. Generate synthetic dataset

```bash
uv run python scripts/generate_data.py
uv run python scripts/load_ofac_data.py --sample
```

### 6. Start the API

```bash
uv run uvicorn app.main:app --reload
# Open: http://localhost:8000/docs
```

### 7. Run tests

```bash
uv run pytest tests/ -v
```

---

## Project structure

```
fraud-ai-investigator/
├── app/
│   ├── agents/         # LangGraph specialist agents (Phase 4)
│   ├── api/            # FastAPI route definitions
│   ├── core/           # Config, logging
│   ├── crypto/         # Etherscan client + mixer detector (crypto branch)
│   ├── graph/          # LangGraph graph definition (Phase 4)
│   ├── llm/            # Gemini + Groq client wrappers (Phase 3)
│   ├── services/       # Alert engine, triage, HITL, fraud memory
│   ├── data/           # Synthetic + IEEE-CIS + OFAC data
│   ├── shared/         # Pydantic models
│   └── main.py
├── dashboard/          # Streamlit UI (Phase 6)
├── notebooks/          # Phase-by-phase EDA and validation (non-production)
├── scripts/            # Data generation and loading scripts
├── tests/              # pytest test suite (~270+ tests)
├── deploy/             # Docker + Hugging Face config (Phase 7)
└── doc/Screenshots/    # Charts saved by notebooks
```

---

## Development roadmap

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Environment, models, synthetic dataset | ✅ Complete |
| Phase 2 | Alert engine (5 rules), REST API, audit trail | ✅ Complete |
| Phase 3 | LLM triage — Gemini scoring + regulatory narratives | ✅ Complete |
| OFAC | Real OFAC SDN sanctions screening, Arabic fuzzy matching | ✅ Complete |
| IEEE-CIS | Real transaction data, unified data loader | ✅ Complete |
| Crypto | Etherscan V2, mixer detection, VARA compliance | ✅ Complete |
| Phase 4 | LangGraph multi-agent investigation (5 parallel agents) | ✅ Complete |
| Phase 5 | HITL review, fraud memory, enhanced audit trail | ✅ Complete |
| Phase 6 | Streamlit dashboard + Docker packaging | ⏳ Planned |
| Phase 7 | Deploy to Hugging Face Spaces (live demo) | ⏳ Planned |

---

## UAE/MENA-specific features

- AED currency with CBUAE AED 40,000 reporting threshold enforcement
- FATF 2024 grey/black list — 20 high-risk jurisdictions
- Real OFAC SDN list (12,000+ entities, updated from treasury.gov)
- Arabic name transliteration matching (Mohammed/Muhammad/Mohammad variants)
- VARA crypto compliance — mixer detection for Dubai-licensed VASPs
- STR (Suspicious Transaction Report) obligation flagging per CBUAE AML/CFT

---

## Contributing

Portfolio project. Issues and PRs welcome.

## License

MIT — see [LICENSE](LICENSE)

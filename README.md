---
title: Fraud AI Investigator
emoji: 🔍
colorFrom: blue
colorTo: red
sdk: docker
pinned: false
license: mit
short_description: Agentic AI fraud investigation for UAE/MENA fintech
---

# Fraud AI Investigator 🔍

> **Production-grade agentic AI system for end-to-end fraud investigation — combining deterministic rule-based alert detection, LLM triage scoring, LangGraph multi-agent parallel investigation, on-chain crypto monitoring, human-in-the-loop governance, and fraud memory. Built on real UAE/MENA AML compliance expertise.**

[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-orange)](https://langchain-ai.github.io/langgraph)
[![Gemini](https://img.shields.io/badge/Gemini_2.5_Flash-free_tier-blue)](https://aistudio.google.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-dashboard-red)](https://streamlit.io)
[![Docker](https://img.shields.io/badge/Docker-containerised-blue)](https://docker.com)
[![Tests](https://img.shields.io/badge/tests-269_passing-brightgreen)](https://github.com/ahmeraza/fraud-ai-investigator)
[![Live](https://img.shields.io/badge/Live_Demo-HuggingFace-yellow)](https://ahmeraza-fraud-ai-investigator.hf.space)

---

## Overview

Fraud analysts at digital payment platforms spend hours manually reviewing transaction alerts, cross-referencing sanctions lists, checking on-chain wallet history, and writing case narratives — for every alert, every day. This system automates the entire investigation pipeline while keeping the analyst in control of the final verdict.

This is not a fraud classifier that outputs a probability score. It is a complete investigation system:

**Alert Layer:** Five deterministic rules fire instantly on every transaction — amount threshold (CBUAE AED 40,000), FATF corridor, device mismatch, new account pattern, OFAC sanctions name match.

**Triage Layer:** Gemini 2.5 Flash scores each alert 0–100 with a regulatory narrative, filtering false positives before they reach the investigation queue.

**Investigation Layer:** A LangGraph graph runs five specialist agents in parallel — transaction analysis, KYC assessment, OFAC sanctions screening, on-chain crypto analysis — then synthesises all findings into a composite risk score and written case narrative informed by past confirmed cases from fraud memory.

**Governance Layer:** The analyst receives a structured review package: investigation summary, similar past cases, regulatory guidance, and an immutable audit trail. Their verdict triggers STR obligation flagging per CBUAE AML/CFT guidelines.

Built with UAE/MENA AML compliance expertise — the rules, thresholds, regulatory flags, and FATF country list reflect real compliance frameworks, not generic placeholders.

**Cost to run: $0/month** — Gemini, Groq, Etherscan all free tier.

---

## Live Demo

**Live Demo:** https://ahmeraza-fraud-ai-investigator.hf.space
**API Docs:** https://ahmeraza-fraud-ai-investigator.hf.space/docs

**Full pipeline — 60 seconds to run:**
```
POST /v1/alerts/generate    → rules fire, alerts created
POST /v1/triage/batch       → Gemini scores each alert 0-100
POST /v1/investigate/batch  → 5 LangGraph agents investigate
GET  /v1/hitl/queue         → analyst sees AWAITING_HUMAN alerts
POST /v1/hitl/{id}/decision → analyst submits verdict
GET  /v1/alerts/{id}/audit  → complete immutable case timeline
```

---

## Architecture

```
Transaction
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│                     ALERT ENGINE                            │
│                                                             │
│  Rule 1: Amount > AED 40,000 (CBUAE reporting threshold)   │
│  Rule 2: Country in FATF 2024 grey/black list (20 nations) │
│  Rule 3: Device fingerprint mismatch (account takeover)     │
│  Rule 4: Account < 30 days + amount > AED 5,000            │
│  Rule 5: Merchant name fuzzy-match vs OFAC SDN (12k names) │
│                                                             │
│  → Alert created: PENDING                                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   LLM TRIAGE (Phase 3)                      │
│                                                             │
│  Gemini 2.5 Flash (primary) → Groq Llama3 (fallback)       │
│  Input: transaction data + KYC profile + trigger context    │
│  Output: severity score 0-100 + UAE regulatory narrative    │
│                                                             │
│  → Score < 30:  AUTO_CLOSED (false positive filtered)       │
│  → Score 30-89: INVESTIGATING                               │
│  → Score ≥ 90:  AWAITING_HUMAN (immediate escalation)       │
└──────────────────────┬──────────────────────────────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     ▼                 ▼                 ▼
┌──────────┐   ┌─────────────┐   ┌──────────────┐
│   KYC    │   │  Sanctions  │   │    Crypto    │ ← parallel
│  Agent   │   │   Agent     │   │    Agent     │   LangGraph
│          │   │             │   │  (if wallet) │   fan-out
└────┬─────┘   └──────┬──────┘   └──────┬───────┘
     │                │                  │
     └────────────────┼──────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│              TRANSACTION AGENT + SYNTHESIS AGENT            │
│                                                             │
│  All signals accumulated via operator.add state reducer     │
│  Fraud memory: similar past cases retrieved and injected    │
│  Gemini synthesises: composite score + case narrative       │
│  MemorySaver checkpointer: full graph state preserved       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    HITL GOVERNANCE                          │
│                                                             │
│  Analyst receives:                                          │
│    • Investigation summary (LLM narrative)                  │
│    • Similar past cases (from fraud memory)                 │
│    • Regulatory guidance (CBUAE/FATF/VARA)                  │
│    • Full audit trail (every event timestamped)             │
│                                                             │
│  Verdicts: CONFIRMED_FRAUD → STR obligation flagged         │
│            FALSE_POSITIVE  → case closed, memory updated    │
│            ESCALATED       → senior analyst queue           │
│            NEEDS_MORE_INFO → re-investigation triggered     │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│               FRAUD MEMORY + AUDIT TRAIL                    │
│                                                             │
│  Every confirmed case stored as structured memory entry     │
│  Future investigations of same customer/corridor/trigger    │
│  retrieve matching past cases → synthesis LLM informed      │
│                                                             │
│  Audit trail: immutable event log from alert creation       │
│  through every state transition to analyst verdict          │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| API backend | FastAPI + Pydantic v2 | Type-safe, auto-documented, production standard |
| Agent orchestration | LangGraph | Stateful parallel graph with checkpointing |
| Primary LLM | Gemini 2.5 Flash (free) | 15 req/min free tier, fast, strong reasoning |
| Fallback LLM | Groq / Llama 3 (free) | Automatic failover, 30 req/min free |
| Sanctions screening | OFAC SDN real data | 12,000+ entities, arabic fuzzy matching |
| Transaction data | IEEE-CIS real dataset | 590k real e-commerce transactions, 3.5% fraud rate |
| Crypto monitoring | Etherscan V2 API (free) | On-chain mixer detection, VARA compliance |
| Dashboard | Streamlit + Plotly | 7-page analyst interface |
| Containerisation | Docker multi-stage | Builder + runtime, non-root user |
| Deployment | Hugging Face Spaces | Free, public, Docker-native |
| Package manager | uv | 10–100x faster than pip |
| Testing | pytest + pytest-cov | 269 tests, all mocked, zero API cost |
| Landing page | Interactive HTML — rotating tags, animated pipeline, flip cards |

---

## UAE/MENA-Specific Design

| Feature | Implementation | Regulatory Basis |
|---|---|---|
| AED reporting threshold | AED 40,000 alert trigger | CBUAE AML/CFT guidelines |
| FATF country list | 20 jurisdictions (2024 grey/black) | FATF mutual evaluation |
| OFAC SDN screening | Real 12k entity list, arabic transliteration | Correspondent banking requirements |
| Arabic name variants | Mohammed/Muhammad/Mohammad fuzzy match | UAE customer demographics |
| STR obligation flag | Auto-triggered on CONFIRMED_FRAUD | CBUAE: STR within 2 working days |
| Crypto mixer detection | Tornado Cash, Blender, Sinbad addresses | VARA Travel Rule compliance |
| FATF new account pattern | Account < 30 days + high value | FATF Recommendation 10 |

---

## Quickstart

### 1 — Clone the repo

```bash
git clone https://github.com/ahmeraza/fraud-ai-investigator.git
cd fraud-ai-investigator
```

### 2 — Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3 — Create environment and install dependencies

```bash
uv venv
source .venv/bin/activate
uv pip install fastapi uvicorn pydantic pydantic-settings faker httpx pytest \
  pytest-cov pytest-dotenv langchain-core google-genai groq langgraph \
  rapidfuzz pandas numpy requests streamlit plotly
```

### 4 — Configure API keys

```bash
cp .env.example .env
# Add to .env — all free, no credit card:
#   GEMINI_API_KEY    → https://aistudio.google.com/apikey
#   GROQ_API_KEY      → https://console.groq.com/keys
#   ETHERSCAN_API_KEY → https://etherscan.io/myapikey  (optional)
```

### 5 — Generate data

```bash
uv run python scripts/generate_data.py
uv run python scripts/load_ofac_data.py --sample
```

### 6 — Run the API

```bash
uv run uvicorn app.main:app --reload
# Open: http://localhost:8000/docs
```

### 7 — Run the dashboard

```bash
uv run streamlit run dashboard/streamlit_app.py
# Open: http://localhost:8501
```

### 8 — Run tests

```bash
uv run pytest tests/ -v
# 269 tests, < 2 seconds
```

---

## Project Structure

```
fraud-ai-investigator/
├── app/
│   ├── agents/                 # LangGraph specialist agents
│   │   ├── transaction_agent.py   # payment data analysis
│   │   ├── kyc_agent.py           # identity risk assessment
│   │   ├── sanctions_agent.py     # OFAC SDN screening
│   │   ├── crypto_agent.py        # on-chain mixer detection
│   │   └── synthesis_agent.py     # LLM final assessment + fraud memory
│   ├── api/                    # FastAPI route definitions
│   │   ├── alerts.py              # alert generation and management
│   │   ├── triage.py              # LLM triage pipeline
│   │   ├── investigation.py       # LangGraph investigation
│   │   ├── hitl.py                # HITL review and verdicts
│   │   └── crypto.py              # on-chain screening
│   ├── core/                   # config, logging
│   ├── crypto/                 # Etherscan V2 client + mixer detector
│   ├── graph/                  # LangGraph graph + shared state
│   │   ├── state.py               # InvestigationState TypedDict
│   │   └── investigation_graph.py # StateGraph compilation
│   ├── llm/                    # Gemini + Groq client wrappers
│   ├── services/               # business logic
│   │   ├── alert_engine.py        # 5 fraud detection rules
│   │   ├── alert_store.py         # in-memory alert store
│   │   ├── data_loader.py         # IEEE-CIS + synthetic unified loader
│   │   ├── fraud_memory.py        # institutional case memory
│   │   ├── hitl_service.py        # verdict processing
│   │   ├── sanctions_screener.py  # OFAC fuzzy name matching
│   │   └── triage_service.py      # LLM triage orchestration
│   ├── shared/
│   │   └── models.py              # Pydantic models (Transaction, Alert, KYC...)
│   ├── data/                   # runtime data (gitignored where large)
│   └── main.py                 # FastAPI application entry point
├── dashboard/
│   └── streamlit_app.py        # 7-page analyst interface
├── notebooks/                  # phase-by-phase EDA and validation
├── scripts/                    # data generation and loading
├── tests/                      # 269 pytest tests
│   ├── test_models.py
│   ├── test_alert_engine.py
│   ├── test_alerts_api.py
│   ├── test_triage.py
│   ├── test_sanctions_screener.py
│   ├── test_ieee_loader.py
│   ├── test_crypto.py
│   ├── test_investigation.py
│   └── test_hitl.py
├── Dockerfile                  # multi-stage build
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## Key Design Decisions

| Decision | Alternative considered | Why this approach |
|---|---|---|
| LangGraph StateGraph over sequential calls | Chain agents with manual state passing | TypedDict state with `operator.add` reducers enables parallel fan-out and MemorySaver checkpointing — HITL interrupts require graph-native state |
| Parallel KYC + Sanctions agents | Sequential investigation | Halves wall-clock time — both data sources are independent, no ordering dependency |
| Separate crypto agent with conditional routing | Always call Etherscan | Most alerts have no crypto wallet — wasting 0.5s per alert on an API call that returns nothing is wrong by design |
| Gemini → Groq automatic fallback | Single provider | Free tiers have rate limits; the system degrades gracefully rather than failing — critical for a pipeline that runs overnight batch jobs |
| OFAC fuzzy matching with Arabic transliteration | Exact string match | Mohammed/Muhammad/Mohammad are the same person. Exact matching misses the most obvious variant — a critical AML compliance failure |
| Fraud memory JSON file over database | SQLite / PostgreSQL | Same Python interface, zero additional infrastructure, trivially swappable — portfolio project should be runnable without a database daemon |
| Rule-based alert engine before LLM triage | Pure LLM classification | Rules are instant, cheap, and auditable. LLM is called only on alerts that pass the rules — reduces LLM calls by ~70% and keeps the expensive path narrow |
| `operator.add` list reducer in state | Last-write-wins | Agents run in parallel and append findings — overwriting would lose evidence from whichever agent writes last |
| MemorySaver checkpointer | No checkpointing | Required for HITL — the graph must be resumable from any node after analyst decision |
| IEEE-CIS real data + synthetic fallback | Synthetic only | Precision/recall against real data is credible. Against synthetic data it is illustrative. The unified loader makes the upgrade transparent |

---

## Development Roadmap

| Milestone | Description | Status |
|---|---|---|
| Phase 1 | Repo setup, Pydantic models, synthetic dataset | ✅ Complete |
| Phase 2 | Alert engine (5 rules), REST API, audit trail | ✅ Complete |
| Phase 3 | LLM triage — Gemini scoring + regulatory narratives | ✅ Complete |
| OFAC | Real OFAC SDN (12k entities), Arabic fuzzy matching | ✅ Complete |
| IEEE-CIS | Real transaction data (590k), unified data loader | ✅ Complete |
| Crypto | Etherscan V2, mixer detection, VARA compliance | ✅ Complete |
| Phase 4 | LangGraph 5-agent parallel investigation graph | ✅ Complete |
| Phase 5 | HITL review, fraud memory, enhanced audit trail | ✅ Complete |
| Phase 6 | Streamlit dashboard + Docker packaging | ✅ Complete |
| Phase 7 | Deploy to Hugging Face Spaces (live demo) | ✅ Complete |

---

## Test Coverage

```
269 tests · < 2 seconds · zero API credits
```

| Test file | Coverage | Approach |
|---|---|---|
| `test_models.py` | Pydantic models, validation, field constraints | Unit |
| `test_alert_engine.py` | All 5 rules, edge cases, OFAC integration | Unit |
| `test_alerts_api.py` | All alert endpoints, data loader mocked | Integration |
| `test_triage.py` | LLM client mocked, state transitions, API | Unit + Integration |
| `test_sanctions_screener.py` | Exact/fuzzy/transliteration matching, offline | Unit |
| `test_ieee_loader.py` | USD→AED conversion, auto/ieee/synthetic modes | Unit |
| `test_crypto.py` | Mixer detection, Etherscan mocked, API | Unit + Integration |
| `test_investigation.py` | LangGraph graph, all 5 agents, MemorySaver | Unit + Integration |
| `test_hitl.py` | Fraud memory, verdict transitions, STR flag | Unit + Integration |

All LLM calls mocked with real `LLMResponse` objects (MemorySaver requires msgpack-serialisable state — MagicMock fails serialisation).

---

## Contributing

Portfolio project. Issues and PRs welcome.

## License

MIT — see [LICENSE](LICENSE)

---

*[GitHub](https://github.com/ahmeraza/fraud-ai-investigator) · [Live Demo](https://ahmeraza-fraud-ai-investigator.hf.space)*
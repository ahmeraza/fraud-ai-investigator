# Learning Reflections — Fraud AI Investigator

> A technical retrospective on the engineering decisions, AI/ML concepts, and production patterns applied across the complete 7-phase pipeline.
> Written to document what was built, why each decision was made, and what it means in a real fintech AML engineering context.

---

## Table of Contents

1. [The Core Insight](#1-the-core-insight)
2. [Alert Engine — Deterministic Rules First](#2-alert-engine--deterministic-rules-first)
3. [LLM Triage — Narrowing the Expensive Path](#3-llm-triage--narrowing-the-expensive-path)
4. [OFAC Sanctions Screening — Arabic Name Matching](#4-ofac-sanctions-screening--arabic-name-matching)
5. [IEEE-CIS Dataset — Why Real Data Changes Everything](#5-ieee-cis-dataset--why-real-data-changes-everything)
6. [Crypto Monitoring — On-Chain AML](#6-crypto-monitoring--on-chain-aml)
7. [LangGraph — Multi-Agent State Management](#7-langgraph--multi-agent-state-management)
8. [HITL Governance — The Human in the Loop](#8-hitl-governance--the-human-in-the-loop)
9. [Production Patterns Applied](#9-production-patterns-applied)
10. [UAE/MENA Regulatory Context](#10-uaemena-regulatory-context)
11. [What This Enables in Fintech](#11-what-this-enables-in-fintech)
12. [Key Lessons](#12-key-lessons)

---

## 1. The Core Insight

> **Fraud detection is not a classification problem. It is an investigation problem.**

The common misconception is that fraud detection means training a model to output `fraud` or `not_fraud`. Production fraud systems at Adyen, Stripe, and Checkout.com work differently: they flag anomalies quickly with rules, then investigate the flagged cases deeply with multiple data sources, then present the evidence to a human analyst for the final decision on high-risk cases.

This system reflects that reality:

```
Rules (instant, cheap, auditable)
  → LLM triage (fast filter, removes obvious false positives)
    → Multi-agent investigation (deep, parallel, expensive — but only for cases that matter)
      → Human analyst (final authority on ambiguous cases)
        → Fraud memory (institutional learning from each decision)
```

The second insight: **the LLM is not the system. The system is the pipeline.** Gemini's quality in the synthesis step depends almost entirely on what the four upstream agents collected — transaction signals, KYC risk flags, sanctions hits, on-chain patterns. A weak agent produces weak synthesis, no matter how good the LLM is.

```
Signal quality → Agent findings → Synthesis quality → Analyst decision quality
Fix at the source. Don't prompt-engineer around bad upstream data.
```

---

## 2. Alert Engine — Deterministic Rules First

**Files:** `app/services/alert_engine.py`, `app/core/config.py`

### 2.1 Why rules before LLMs

Every LLM call costs time (0.5–3 seconds) and rate limit quota (Gemini free: 15/min). Running an LLM on every transaction is both slow and unnecessary — 95% of transactions are clearly not suspicious by any regulatory definition.

The alert engine applies five deterministic rules in milliseconds:

```python
Rule 1: amount_aed > 40_000                          # CBUAE threshold
Rule 2: country in FATF_2024_HIGH_RISK_COUNTRIES     # 20 jurisdictions
Rule 3: device_id != last_known_device               # account takeover signal
Rule 4: account_age_days < 30 and amount > 5_000     # money mule pattern
Rule 5: fuzzy_match(merchant, OFAC_SDN_LIST) >= 75   # sanctions evasion
```

Only alerts that pass these rules proceed to LLM triage. This reduces LLM calls by approximately 70% depending on the transaction mix.

### 2.2 The CBUAE AED 40,000 threshold

The UAE Central Bank AML/CFT guidelines require reporting of cash transactions above AED 40,000. This is not a threshold chosen to make the demo work — it is the actual regulatory number. Building it as a named constant in `config.py` rather than a hardcoded literal means it can be updated if regulations change without touching rule logic.

### 2.3 OFAC fuzzy matching as a rule, not an investigation step

Running the full OFAC fuzzy screener at alert creation time (Rule 5) catches a class of case that pure rule-based systems miss: sanctioned entities operating through third-country front companies. A payment to `Gulf Resources General Trading LLC` (UAE-registered) with `country=AE` passes the FATF corridor rule. But the OFAC SDN list contains that exact entity. The fuzzy screener catches it at rule time, not investigation time.

This is the correct production pattern. Investigation-time sanctions screening (what the sanctions agent does) uses a lower threshold (50, PROBABLE match) and catches more variants. Alert-time screening (75, STRONG match) is a first-pass filter.

---

## 3. LLM Triage — Narrowing the Expensive Path

**Files:** `app/llm/client.py`, `app/llm/prompts.py`, `app/services/triage_service.py`

### 3.1 Provider fallback architecture

The LLM client is a unified interface with automatic fallback:

```
Request → GeminiProvider (primary)
              ↓ (if fails or rate-limited)
          GroqProvider (fallback)
              ↓ (if fails)
          RuntimeError (captured, alert reverts to PENDING)
```

Key design decisions:
- **Exponential backoff**: 2s → 4s between retries per provider. This is the standard production pattern for unreliable external APIs.
- **Alert never lost**: on any LLM failure, the alert reverts to PENDING and can be retried. A failed triage call does not corrupt state.
- **Singleton client**: `get_llm_client()` returns the same instance. Provider initialisation (API key validation, model loading) happens once at startup, not per request.

### 3.2 Structured JSON output

Every triage call requires the LLM to respond in a specific JSON schema:

```json
{
  "severity_score": 75,
  "severity_band": "HIGH",
  "initial_suspicion": "...",
  "risk_factors": ["...", "..."],
  "recommended_action": "INVESTIGATE",
  "regulatory_flags": ["CBUAE STR required if confirmed"],
  "confidence": "HIGH"
}
```

This is validated by Pydantic before any state change. Malformed LLM responses raise `ValidationError` — the alert reverts to PENDING and the failed response is logged. This is the production pattern: never trust unvalidated LLM output to drive state transitions.

### 3.3 UAE regulatory context in the system prompt

The system prompt is not generic. It encodes UAE-specific knowledge:

- CBUAE reporting threshold: AED 40,000
- FATF high-risk jurisdiction list with country codes
- New account (<30 days) + large transaction = FATF money mule pattern
- Device mismatch = account takeover indicator

This domain encoding is why the LLM's narratives contain actionable regulatory language rather than generic fraud commentary.

---

## 4. OFAC Sanctions Screening — Arabic Name Matching

**Files:** `app/services/sanctions_screener.py`, `scripts/load_ofac_data.py`

### 4.1 Why exact matching fails for MENA names

Arabic names romanised into English have no single standard spelling. The same individual appears as Mohammed Al-Rashidi, Muhammad Al Rashidi, and Mohammad Rashid across different KYC documents, passports, and transaction records. An exact-match screener treats these as three different people. A fuzzy screener with Arabic transliteration normalisation treats them as one.

The normalisation map handles the most common variants:

```python
ARABIC_MAP = {
    "mohammed": "muhammad", "mohammad": "muhammad", "mohamad": "muhammad",
    "al-": "al ",  "el-": "al ",  "bin ": "ibn ",
    "hassan": "hasan", "hussain": "husayn", "hussein": "husayn",
    ...
}
```

After normalisation, `Mohammed Al-Rashidi` and `Muhammad Al Rashidi` both become `muhammad al rashidi` before fuzzy comparison. The fuzzy match score reflects genuine similarity rather than spelling variation.

### 4.2 Three-pass screening

```
Pass 1: Exact match on normalised name → score 100, immediate hit
Pass 2: rapidfuzz token_set_ratio across all 40k+ name variants → top 20 candidates
Pass 3: Token overlap fallback for partial names (at least 2 tokens matching)
```

rapidfuzz (Rust-backed) searches 40,000 name variants in under 100ms. This is fast enough for real-time transaction screening.

### 4.3 Real OFAC SDN data

The screener loads real data from the US Treasury (`sdn.xml`, ~25MB, ~12,000 entities). The `--sample` flag provides 5 fictional entities for offline development. The production screener checks every transaction merchant name and customer name against the real list.

The OFAC SDN list is updated by Treasury continuously. The download script caches it for 24 hours. Production deployments would update daily via a cron job.

---

## 5. IEEE-CIS Dataset — Why Real Data Changes Everything

**Files:** `app/services/data_loader.py`, `scripts/load_ieee_data.py`

### 5.1 What changes with real data

Synthetic Faker data has perfectly clean distributions by design:
- Fraud rate: configurable (we set 20%)
- Amount distribution: uniform within a range
- Country distribution: proportional to config

IEEE-CIS real data has messy, real-world characteristics:
- Fraud rate: 3.5% (vs 20% synthetic) — much harder classification
- Amount distribution: heavily right-skewed (most transactions are small)
- 434 features vs 8 synthetic features
- Missing values: 0.1%–99% per feature depending on device/identity data

The consequence: precision/recall numbers against IEEE-CIS data are credible in an interview. Against synthetic data they are illustrative. The difference matters when someone asks "what's your system's false positive rate?"

### 5.2 The unified data loader pattern

The data loader implements a progressive enhancement pattern:

```python
source="auto"  # IEEE-CIS if available, else synthetic
source="ieee"  # IEEE-CIS only, raise if missing
source="synthetic"  # always synthetic
source="combined"  # both sources merged
```

This means the system works on Day 1 without any Kaggle download, but upgrades automatically the moment real data is placed in `app/data/ieee_cis/`. No code changes, no config changes, no restart needed.

### 5.3 Feature engineering for the pipeline

The IEEE-CIS dataset has no `country` field — it has `addr2` (numeric country code) and `TransactionDT` (seconds from a reference timestamp). Converting these to pipeline-compatible fields requires domain decisions:

- `TransactionDT` → actual timestamp: community analysis identified the reference as 2017-11-30
- `addr2` → ISO country code: mapping known numeric codes to ISO-2 format
- `D1` (days since card first used) → `account_age_days`: direct proxy
- `id_28` ("New" / "Found") → `device_is_new`: binary signal for device mismatch

These conversions are documented in the script. Undocumented feature engineering is the most common source of silent bugs in ML pipelines.

---

## 6. Crypto Monitoring — On-Chain AML

**Files:** `app/crypto/etherscan_client.py`, `app/crypto/mixer_detector.py`, `app/crypto/crypto_alert_engine.py`

### 6.1 Why crypto monitoring is different from payment AML

Traditional payment AML has a clear data source: the payment network provides transaction records with counterparty details. Crypto AML has a different structure: all transactions are public on-chain, but the relationship between wallet addresses and real-world identities is opaque.

The mixer detector operates in this opaque space:

**Layer 1 (known addresses):** Direct interaction with OFAC-sanctioned mixer contracts — Tornado Cash (sanctioned August 2022), Blender (sanctioned May 2022), Sinbad (sanctioned November 2023). These are real contract addresses from public OFAC records. A transaction to or from these addresses scores 70 immediately.

**Layer 2 (behavioural patterns):** Round ETH amounts matching Tornado Cash pool denominations (0.1, 1, 10, 100 ETH), rapid in/out patterns within 1 hour (layering), high internal transaction ratio (contract hopping). These patterns can identify mixer usage even when the mixer address itself isn't in the known list.

### 6.2 The Etherscan V2 rate limiting solution

The free tier allows 5 requests/second. The client enforces a 250ms minimum gap between requests:

```python
elapsed = time.monotonic() - self._last_req
if elapsed < MIN_REQUEST_GAP:
    time.sleep(MIN_REQUEST_GAP - elapsed)
```

This is the correct pattern: measure actual elapsed time rather than sleeping a fixed duration. If a request takes 400ms, no additional sleep is needed. If it takes 50ms, sleep 200ms more. This maximises throughput within the rate limit.

### 6.3 VARA compliance context

Dubai's Virtual Assets Regulatory Authority (VARA) requires VASPs to screen wallet addresses against OFAC sanctions lists under Travel Rule compliance. The mixer detection module provides exactly this capability. UAE is one of the highest crypto adoption regions globally — this is not a hypothetical regulatory requirement.

---

## 7. LangGraph — Multi-Agent State Management

**Files:** `app/graph/state.py`, `app/graph/investigation_graph.py`, `app/agents/`

### 7.1 Why LangGraph over a sequential function chain

A sequential chain would work for simple cases:

```python
tx_result = transaction_agent(alert)
kyc_result = kyc_agent(alert)
sanctions_result = sanctions_agent(alert)
final = synthesis_agent(tx_result, kyc_result, sanctions_result)
```

LangGraph enables three things a function chain cannot:

**Parallel execution:** KYC and sanctions agents run simultaneously — they are independent data sources with no ordering dependency. This halves wall-clock investigation time vs sequential execution.

**Stateful checkpointing:** MemorySaver checkpoints the full state after every node. The HITL interrupt (Phase 5) works because the graph can be paused at a node boundary and resumed after analyst input — impossible with a function chain.

**Conditional routing:** The crypto agent only runs when a wallet address is present. A function chain would require explicit if/else in the orchestrator. LangGraph's `add_conditional_edges` encodes this routing logic in the graph structure where it belongs.

### 7.2 The operator.add reducer — the most important design decision

The `InvestigationState` TypedDict uses annotated reducers for list fields:

```python
findings    : Annotated[list[dict], operator.add]
risk_signals: Annotated[list[str],  operator.add]
```

Without the reducer, parallel agents writing to the same field would produce a race condition — the last write wins and overwrites earlier agents' findings.

With `operator.add`, LangGraph merges each agent's output by appending to the accumulated list. The final state contains findings from all agents regardless of execution order. This is the correct pattern for any multi-agent system where parallel agents produce evidence.

### 7.3 MemorySaver and msgpack serialisation

MemorySaver checkpoints state using msgpack serialisation. Everything stored in `InvestigationState` must be a plain Python type — `str`, `int`, `float`, `list`, `dict`, `None`. This constraint produced the most subtle bug in the project:

Test mocks used `MagicMock()` as the LLM response. The synthesis agent stored metadata from the response into state. During the graph test, MemorySaver tried to serialise the state — including the MagicMock — and crashed with `TypeError: Type is not msgpack serializable`.

The fix: replace `MagicMock()` with real `LLMResponse` objects in graph tests. The agent only stores plain scalars from the response into state, never the response object itself.

This is a non-obvious constraint that isn't documented in the LangGraph quickstart. Finding it through debugging is the difference between understanding the framework and copying its examples.

---

## 8. HITL Governance — The Human in the Loop

**Files:** `app/services/hitl_service.py`, `app/services/fraud_memory.py`, `app/api/hitl.py`

### 8.1 Why HITL is not optional in AML

Fully automated fraud systems have two failure modes that are unacceptable in a regulated environment:

**False positive overblocking:** An automated system blocks a legitimate high-value transfer. The customer complains. The bank has no evidence of the analyst's reasoning because there was no analyst. The regulator asks for the investigation record — it doesn't exist.

**Missed fraud:** An automated system clears a transaction that was fraud. The STR wasn't filed within 2 working days because the system didn't flag it for human review. CBUAE fines the bank.

HITL solves both: high-risk cases escalate to a human analyst who can exercise judgment and whose decision is logged with timestamp, rationale, and evidence considered. The audit trail is the regulatory record.

### 8.2 Fraud memory as institutional learning

Every analyst verdict is stored as a structured memory entry:

```json
{
  "memory_id": "MEM-alert-001",
  "verdict": "CONFIRMED_FRAUD",
  "customer_id": "CUST001",
  "trigger": "SANCTIONED_CORRIDOR",
  "country": "IR",
  "risk_score": 85,
  "analyst_notes": "OFAC SDN match confirmed...",
  "risk_signals": ["High value AED 55k", "Iran FATF corridor"],
  "recorded_at": "2026-05-22T17:30:00Z"
}
```

The synthesis agent retrieves similar past cases before calling the LLM:

```python
past_cases = retrieve_similar_cases(
    customer_id = state["customer_id"],  # same customer → +10 score
    country     = state["country"],       # same corridor → +5 score
    trigger     = state["trigger"],       # same trigger  → +3 score
)
```

The LLM is then told: "The same customer was previously confirmed as fraud" or "A similar corridor transaction was a false positive." This historical context meaningfully shifts the composite score without any model retraining. The system gets smarter with every analyst decision.

### 8.3 STR obligation — regulatory automation

When an analyst submits `CONFIRMED_FRAUD`, the response includes:

```json
{
  "str_required": true,
  "str_deadline": "Within 2 working days per CBUAE AML/CFT guidelines"
}
```

This is not a feature added for completeness. CBUAE requires banks to file Suspicious Transaction Reports within 2 working days of confirming suspicious activity. Automating the obligation flag — and surfacing it prominently in the analyst interface — is the difference between a demo and a system someone would actually use.

---

## 9. Production Patterns Applied

### 9.1 Lazy initialisation

Every service in the pipeline uses lazy initialisation:

```python
_engine: Optional[AlertEngine] = None

def _get_engine() -> AlertEngine:
    global _engine
    if _engine is None:
        _engine = AlertEngine()
    return _engine
```

This avoids initialising services at module import time. If a service's dependencies aren't available (no API key, no data file), the error surfaces when the endpoint is called — not when the module is imported. This makes tests that mock services work correctly.

The triage service initialised eagerly at module load was the root cause of the `RuntimeError: No LLM providers available` test failure. Lazy initialisation fixed it permanently.

### 9.2 The alert never gets lost

Every service interaction with an alert follows this pattern:

```python
alert.status = AlertStatus.TRIAGING
store.save(alert)
try:
    result = do_expensive_thing(alert)
    alert.status = AlertStatus.INVESTIGATING
    store.save(alert)
except Exception as e:
    alert.status = AlertStatus.PENDING  # ← always reverts
    store.save(alert)
    log_error(e)
```

An alert in TRIAGING or INVESTIGATING state that encounters an error always reverts to PENDING. This means the alert can be retried without manual intervention, and no alert is ever silently lost in a failed state.

### 9.3 Route ordering in FastAPI

FastAPI matches routes in registration order. `/{alert_id}` registered before `/batch` means `POST /v1/investigate/batch` is matched as `alert_id="batch"` — returning 404.

The rule: always register static routes (`/batch`, `/stats`, `/queue`) before parameterised routes (`/{alert_id}`). This produced three test failures across Phase 4 and Phase 5 before the pattern was understood and consistently applied.

### 9.4 Test isolation with tmp_path

The fraud memory tests use pytest's `tmp_path` fixture to redirect writes to a temporary file:

```python
@pytest.fixture(autouse=True)
def clean_state(tmp_path):
    memory_file = tmp_path / "fraud_memory.json"
    with patch("app.services.fraud_memory.MEMORY_PATH", memory_file):
        yield
```

This ensures tests don't share memory state, and no test creates permanent files on disk. Patching only `app.services.fraud_memory.MEMORY_PATH` (not `hitl_service.MEMORY_PATH` — which doesn't exist) required understanding which module owns the constant. `hitl_service` imports functions from `fraud_memory`, not the path constant — so patching the source module is sufficient for all callers.

### 9.5 msgpack serialisation constraint in LangGraph tests

LangGraph's MemorySaver checkpoints state using msgpack. The constraint: everything in `InvestigationState` must be a plain Python type. This is not documented in the LangGraph quickstart.

The failure produced the error: `TypeError: Type is not msgpack serializable: MagicMock`.

The solution: use real `LLMResponse` objects in graph tests, never MagicMock as a return value that gets stored in state. The agent can receive a MagicMock LLM client, but the client's return value must be a real `LLMResponse`:

```python
# Wrong — MagicMock gets stored in state findings
mock_llm.complete.return_value = MagicMock()

# Correct — real LLMResponse, msgpack-serialisable
mock_llm.complete.return_value = LLMResponse(
    content=json.dumps(MOCK_SYNTHESIS_JSON),
    provider="gemini", latency_ms=1000.0, model="test"
)
```

### 9.6 HTML landing page as a module-level constant

The landing page HTML is stored as `LANDING_HTML` at module level in `main.py`, not inside the route function. Two reasons: the string is parsed once at import time rather than on every request; and it keeps the route function to a single readable line. For strings this large, embedding them in the function body creates noise around the function signature.

The page itself encodes a production consideration: the Hugging Face Space URL is the first thing a recruiter or engineer sees. A raw JSON response (`{"service": "Fraud AI Investigator"}`) communicates nothing. An interactive page with rotating fintech keywords, an animated investigation pipeline, flip cards showing each module, and clickable endpoint links communicates the scope of the system in under 10 seconds without requiring the visitor to read documentation.

This is the same reason production APIs at Stripe and Twilio have polished developer landing pages — the landing page is part of the product, not an afterthought.

---

## 10. UAE/MENA Regulatory Context

| Regulation | Requirement | Implementation |
|---|---|---|
| CBUAE AML/CFT | STR within 2 working days of confirming suspicious activity | `str_required` flag on CONFIRMED_FRAUD verdict |
| CBUAE AML/CFT | Cash transactions > AED 40,000 must be reported | Rule 1 alert threshold |
| FATF Recommendation 10 | Enhanced due diligence for high-risk jurisdictions | FATF 2024 20-country list in config |
| FATF Recommendation 10 | New customer + large transaction = EDD required | Rule 4: account < 30 days |
| OFAC sanctions | US-connected transactions cannot involve SDN entities | Rule 5 + sanctions agent |
| VARA Travel Rule | VASPs must screen wallets against sanctions | Crypto mixer detection module |
| FATF Recommendation 15 | VASPs must apply AML/CFT measures to virtual assets | Full crypto monitoring pipeline |

The domain specificity of these rules — the exact threshold, the exact country codes, the specific OFAC programs most relevant to MENA — reflects experience with the regulatory environment, not research conducted for the project.

---

## 11. What This Enables in Fintech

### 11.1 The evidence problem in financial AI

A fraud system that says "this transaction is suspicious" with no explanation is useless in a regulated environment. A compliance officer cannot file a STR based on an unexplained score. A system that says "score 85: AED 55,000 transfer to Iran-registered entity, merchant name matches OFAC SDN entity Gulf Resources FZE (score 92/100, IFSR/IRAN programs), customer device mismatch on 15-day-old account — CBUAE STR required if confirmed" is actionable because it is verifiable.

Every design decision in this system is oriented toward this:
- Agent findings make every signal traceable to its source
- Pydantic validation ensures LLM outputs are structured before state changes
- Audit trail captures every event with actor, timestamp, and metadata
- Fraud memory makes past analyst reasoning available for future cases
- HITL context package gives the analyst everything needed to make a documented decision

### 11.2 Why the rule-based layer is as important as the LLM layer

A common mistake in ML-based fraud systems is removing the rule layer when an ML model is added. Rules are fast, cheap, and auditable — qualities that matter in a regulated environment. A model that classifies transactions cannot explain why it made a decision in the terms a regulator accepts. A rule that fires because `amount > 40000` can.

The correct architecture — used at Adyen, Stripe, and Checkout.com — keeps both: rules for the 70% of clearly non-suspicious transactions, models for the 30% that require deeper assessment. This system implements exactly this two-tier architecture.

### 11.3 Fraud memory as a moat

Most fraud detection systems treat each case in isolation. This system accumulates institutional knowledge: every confirmed fraud case adds a memory entry that informs future investigations of the same customer, same corridor, or same trigger pattern. The system gets better over time without any model retraining — just analyst decisions flowing through the HITL pipeline.

This is the pattern that separates a fraud system from a fraud model.

---

## 12. Key Lessons

**1. Fraud detection is an investigation problem, not a classification problem**
The correct architecture is: rules → triage → investigation → human verdict. Not: model → score → block/clear. The human analyst with full context makes better decisions than any model alone, and the audit trail makes those decisions defensible.

**2. The LLM is the last 10%**
Alert rules, OFAC data loading, sanctions fuzzy matching, Etherscan data fetching, LangGraph state management, fraud memory retrieval — all of this happens before the LLM is called. The LLM synthesises what the pipeline collected. Getting the pipeline right determines synthesis quality.

**3. Parallel execution requires correct state management**
`operator.add` reducers are not an implementation detail — they are the architectural decision that makes parallel agent execution correct. Without them, parallel writes to the same state field produce non-deterministic results depending on execution order.

**4. Every state transition must be safe to retry**
Any service that transitions alert status must handle failure by reverting to the previous state. An alert stuck in TRIAGING because the LLM timed out is a data integrity problem. The pattern — save intermediate state, try, revert on failure — prevents this class of bug.

**5. Route ordering in FastAPI is a design decision**
Static routes must be registered before parameterised routes. This is not documented prominently in FastAPI's docs. Discovering it through three separate test failures (phases 4, 5, HITL) embedded it permanently.

**6. Real data changes the problem**
IEEE-CIS's 3.5% fraud rate vs synthetic's 20% fraud rate is not just a number difference — it changes the classification problem entirely. At 3.5% prevalence, a 95% accurate model has a false discovery rate of ~50%. Domain knowledge about class imbalance is essential to interpreting model metrics correctly.

**7. Domain expertise in the prompt is not prompt engineering**
Encoding UAE regulatory thresholds, FATF country codes, and CBUAE STR requirements in the system prompt is domain knowledge, not prompt engineering. The difference: domain knowledge is correct because it reflects reality; prompt engineering is optimisation for output format. Both matter, but domain knowledge is the harder one to acquire.

**8. msgpack serialisation is a hidden LangGraph constraint**
LangGraph's MemorySaver serialises state with msgpack. Everything in state must be a plain Python type. This is not in the quickstart documentation. Every MagicMock in a graph test that reaches state will fail serialisation with an opaque error. Discovering this constraint through debugging is worth documenting explicitly.

**9. Tests reveal architecture problems**
The lazy initialisation fix, the route ordering fix, the `tmp_path` patch fix, the `operator.add` reducer design — all of these were identified through writing tests, not through upfront design review. Tests are not just verification — they are the fastest feedback loop for architectural problems.

**10. Regulatory context is the differentiation**
Any engineer can build a transaction scoring API. The UAE/MENA regulatory specificity — CBUAE thresholds, FATF 2024 country list, VARA Travel Rule, Arabic name transliteration — is what makes this system relevant to the problems UAE-based fintech companies actually face. Technical skill without domain expertise builds systems that work but don't matter for the specific context they operate in.

---

*[GitHub](https://github.com/ahmeraza/fraud-ai-investigator) · [Live Demo](https://ahmeraza-fraud-ai-investigator.hf.space/docs)*
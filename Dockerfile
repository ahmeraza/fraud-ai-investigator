# Dockerfile
# ─────────────────────────────────────────────────────────────
# Fraud AI Investigator — Production Docker image
#
# Multi-stage build:
#   Stage 1 (builder): installs Python dependencies
#   Stage 2 (runtime): copies only what's needed — smaller image
#
# Local:  docker build -t fraud-ai-investigator .
#         docker run -p 7860:7860 --env-file .env fraud-ai-investigator
# HF:     Automatically built by Hugging Face Spaces
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml .
COPY uv.lock* .

RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python \
    fastapi uvicorn pydantic pydantic-settings \
    langchain-core google-genai groq langgraph \
    rapidfuzz pandas numpy requests httpx \
    streamlit plotly faker

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY app/       ./app/
COPY dashboard/ ./dashboard/
COPY scripts/   ./scripts/

RUN mkdir -p app/data/sanctions \
             app/data/ieee_cis  \
             app/data/crypto    \
             app/data/fraud_memory

# Generate synthetic data at build time so Space works immediately
RUN python scripts/generate_data.py 2>/dev/null || true
RUN python scripts/load_ofac_data.py --sample 2>/dev/null || true

# Non-root user for security (required by Hugging Face)
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Port 7860 — required by Hugging Face Spaces
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]

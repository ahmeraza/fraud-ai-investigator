# Dockerfile
# ─────────────────────────────────────────────────────────────
# Fraud AI Investigator — Production Docker image
#
# Multi-stage build:
#   Stage 1 (builder): installs Python dependencies
#   Stage 2 (runtime): copies only what's needed — smaller image
#
# Build:  docker build -t fraud-ai-investigator .
# Run:    docker run -p 8000:8000 --env-file .env fraud-ai-investigator
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv (fast package installer)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (Docker layer caching)
# If these don't change, the pip install layer is reused
COPY pyproject.toml .
COPY uv.lock* .

# Install dependencies into a virtual environment
RUN uv venv /opt/venv && \
    uv pip install --python /opt/venv/bin/python \
    fastapi uvicorn pydantic pydantic-settings \
    langchain-core google-genai groq langgraph \
    rapidfuzz pandas numpy requests httpx \
    streamlit plotly

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY app/       ./app/
COPY dashboard/ ./dashboard/
COPY scripts/   ./scripts/

# Create data directories (populated at runtime or via volume)
RUN mkdir -p app/data/sanctions \
             app/data/ieee_cis  \
             app/data/crypto

# Non-root user for security
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# Expose API port
EXPOSE 8000

# Health check — Docker will restart the container if this fails
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Default: run the API
# Override with: docker run ... streamlit run dashboard/streamlit_app.py
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

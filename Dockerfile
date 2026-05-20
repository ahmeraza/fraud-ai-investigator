# ── Fraud AI Investigator — Dockerfile ────────────────────────────────────────
#
# Single-image build that runs both:
#   - FastAPI backend on port 8000 (internal only)
#   - Streamlit dashboard on port 8501 (public)
#
# Deploy to Hugging Face Spaces:
#   1. Create a Docker Space at huggingface.co/new-space
#   2. Set app_port: 8501 in Space settings
#   3. Add GEMINI_API_KEY and GROQ_API_KEY as Space Secrets
#   4. Push this repo — auto-deploys on every push
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y \
    curl \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files first (better Docker layer caching)
COPY pyproject.toml .
COPY README.md .

# Install dependencies
RUN uv venv && uv sync --no-dev

# Copy application code
COPY app/ ./app/
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

# Generate synthetic data at build time so the demo works out of the box
RUN uv run python scripts/generate_data.py

# Supervisor config to run both services
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8000 8501

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

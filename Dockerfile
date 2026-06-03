# ─────────────────────────────────────────────────────────────────────────────
# Agentic IAM — Dockerfile
#
# Single image used by all three services (streamlit, user-watcher, app-watcher).
# The CMD is overridden per service in docker-compose.yml.
#
# Build:  docker build -t agentic-iam .
# Run:    docker-compose up --build
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Metadata
LABEL maintainer="LabsKraft"
LABEL description="Agentic IAM — AI-powered Identity and Access Management"

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        gcc \
        libffi-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first so Docker can cache this layer.
# Re-runs only when requirements.txt changes.
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Watched folder structure ──────────────────────────────────────────────────
# Create all inbox/processing/processed/failed directories.
# In production, these are mounted as volumes from docker-compose.
RUN mkdir -p \
    watched_inbox \
    watched_processing \
    watched_processed \
    watched_failed \
    watched_apps_inbox \
    watched_apps_processing \
    watched_apps_processed \
    watched_apps_failed

# ── Streamlit config ──────────────────────────────────────────────────────────
# Disable Streamlit's browser auto-open and usage stats in container
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# ── Expose Streamlit port ─────────────────────────────────────────────────────
EXPOSE 8501

# ── Default command — overridden per service in docker-compose.yml ────────────
CMD ["streamlit", "run", "frontend/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]


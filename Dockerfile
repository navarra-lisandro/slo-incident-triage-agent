# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Dockerfile for slo-incident-triage-agent
#
# Multi-stage build:
#   builder   installs dependencies via Poetry
#   runtime   lean production image without build tools
#
# Python version matches .python-version and pyproject.toml (ADR-001)
# Uses python:3.11.9-slim-bookworm Debian Bookworm base — slightly more libs than plain old slim, but
#                        still lean, better compatibility for our dependencies, and security updates via Debian backports
#
# Build:
#   docker build -t slo-incident-triage-agent .
#
# Run:
#   docker run --env-file .env -p 8000:8000 slo-incident-triage-agent
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stage 1 — builder
# Installs Poetry and project dependencies into /app/.venv
# ---------------------------------------------------------------------------
FROM python:3.11.9-slim-bookworm AS builder

WORKDIR /app

# install Poetry — pinned to match local development environment (ADR-002)
RUN pip install --no-cache-dir poetry==2.3.2

# copy dependency files first for layer caching
# changes to source code won't invalidate this layer
COPY pyproject.toml poetry.lock ./

# install dependencies into a virtual environment inside the project
# --no-root: do not install the project package itself (ADR-002)
# --without dev: exclude dev dependencies from production image
RUN poetry config virtualenvs.in-project true && \
    poetry install --no-root --without dev --no-interaction

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# Lean production image — no Poetry, no build tools
# ---------------------------------------------------------------------------
FROM python:3.11.9-slim-bookworm AS runtime

WORKDIR /app

# create non-root user for security
RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser

# copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

# copy application code
COPY agent/ ./agent/
COPY data/ ./data/

# ensure the venv Python is used
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"

# do not buffer Python output — ensures logs appear immediately
ENV PYTHONUNBUFFERED=1

# run as non-root user
USER appuser

# expose FastAPI port
EXPOSE 8000

# health check — uses the /health endpoint (api.py)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# default command — run FastAPI via uvicorn
# workers=1 for single-process container (scale via K8s replicas)
CMD ["uvicorn", "agent.api:app", "--host", "0.0.0.0", "--port", "8000"]

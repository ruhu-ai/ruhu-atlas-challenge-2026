# Backend container — used by both AWS (ECS Fargate) and GCP (Cloud Run).
# Multi-stage build: install deps in a builder image, copy only the runtime
# slice into the final image. Final image runs as non-root.

# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build deps for psycopg / cryptography / etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy package metadata first so dependency resolution layer caches well
COPY pyproject.toml ./
COPY src ./src

# Install with the api + dev extras the production server needs.
# `[api]` pulls FastAPI + uvicorn + psycopg etc; drop `[dev]` for prod-only.
RUN pip install --prefix=/install ".[api]"

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# Runtime libs only — no compilers in the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system ruhu \
    && useradd --system --gid ruhu --create-home --home-dir /home/ruhu ruhu

WORKDIR /app

# Bring installed packages + console scripts from the builder
COPY --from=builder /install /usr/local

# App source. We copy `src/` rather than relying on the editable install
# from the builder because the runtime image needs the importable package
# at /app/src per PYTHONPATH above.
COPY --chown=ruhu:ruhu src ./src
COPY --chown=ruhu:ruhu alembic ./alembic
COPY --chown=ruhu:ruhu alembic.ini ./alembic.ini

USER ruhu

EXPOSE 8000

# Cloud Run + ECS Fargate both expect the container to listen on $PORT
# (Cloud Run injects it; Fargate task def sets it). Default to 8000.
ENV PORT=8000

# Health check at /health — already implemented in api.py
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["sh", "-c", "uvicorn ruhu._dev_server:create_app --factory --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips=*"]

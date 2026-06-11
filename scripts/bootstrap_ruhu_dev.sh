#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"

DB_NAME="${RUHU_DEV_DB_NAME:-ruhu_runtime_dev}"
DB_HOST="${RUHU_DEV_DB_HOST:-localhost}"
DB_PORT="${RUHU_DEV_DB_PORT:-5432}"
DB_USER="${RUHU_DEV_DB_USER:-postgres}"
DB_PASSWORD="${RUHU_DEV_DB_PASSWORD:-postgres}"
ADMIN_DB="${RUHU_DEV_ADMIN_DB:-postgres}"

DEFAULT_DATABASE_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
DATABASE_URL="${RUHU_DATABASE_URL:-$DEFAULT_DATABASE_URL}"
AUTH_DATABASE_URL="${RUHU_AUTH_DATABASE_URL:-$DATABASE_URL}"
ADMIN_DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${ADMIN_DB}"

if ! psql "${ADMIN_DATABASE_URL}" -tAc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1; then
  psql "${ADMIN_DATABASE_URL}" -c "CREATE DATABASE \"${DB_NAME}\""
fi

CURRENT_REVISION="$(psql "${DATABASE_URL}" -tAc "SELECT version_num FROM alembic_version LIMIT 1" 2>/dev/null | tr -d '[:space:]' || true)"
PUBLIC_TABLE_COUNT="$(psql "${DATABASE_URL}" -tAc "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'" 2>/dev/null | tr -d '[:space:]' || true)"

if [ -n "${CURRENT_REVISION}" ]; then
  if ! grep -Rqs "revision = \"${CURRENT_REVISION}\"" "${REPO_ROOT}/alembic/versions"; then
    echo "Database ${DB_NAME} is stamped with Alembic revision ${CURRENT_REVISION}, which does not belong to this repo." >&2
    echo "Point RUHU_DATABASE_URL at a fresh database or override RUHU_DEV_DB_NAME with a clean database name." >&2
    exit 1
  fi
elif [ "${PUBLIC_TABLE_COUNT:-0}" != "0" ]; then
  echo "Database ${DB_NAME} already contains public tables but is not stamped for this repo." >&2
  echo "Use a fresh database or clear the existing one before running bootstrap." >&2
  exit 1
fi

if [ ! -x "${PYTHON_BIN}" ]; then
  echo "Missing ${PYTHON_BIN}. Run 'make install' from ${REPO_ROOT} first." >&2
  exit 1
fi

# ruhu.db.run_migrations handles fresh databases (create_all + stamp head)
# as well as upgrades; raw `alembic upgrade head` cannot build from empty
# because ORM-only tables are altered by later migrations.
PYTHONPATH=src \
RUHU_DATABASE_URL="${DATABASE_URL}" \
RUHU_AUTH_DATABASE_URL="${AUTH_DATABASE_URL}" \
"${PYTHON_BIN}" -c "import os; from ruhu.db import run_migrations; run_migrations(os.environ['RUHU_DATABASE_URL'])"

printf '\n%s is ready.\n' "${DB_NAME}"
printf 'RUHU_DATABASE_URL=%s\n' "${DATABASE_URL}"
printf 'RUHU_AUTH_DATABASE_URL=%s\n' "${AUTH_DATABASE_URL}"

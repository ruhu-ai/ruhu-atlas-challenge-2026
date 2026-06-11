"""Dev server factory — used with `uvicorn ruhu._dev_server:create_app --factory`."""
from __future__ import annotations

import os
from pathlib import Path

from ruhu.api import build_default_app
from ruhu.db import resolve_database_url, run_migrations
from ruhu.env_files import load_env_file


def _load_dev_environment(repo_root: Path) -> None:
    # `load_env_file(..., override=False)` preserves explicit shell exports, so
    # load the most specific local files first and let broader fallbacks fill gaps.
    candidate_paths = [
        repo_root / ".env.development.local",
        repo_root / ".env.local",
        repo_root / ".env.development",
        repo_root / ".env",
    ]
    env_file_override = os.getenv("RUHU_DEV_ENV_FILE")
    if env_file_override:
        candidate_paths.append(Path(env_file_override).expanduser())
    for env_path in candidate_paths:
        if env_path.exists():
            load_env_file(env_path, override=False)


def _dev_auto_migrate_enabled() -> bool:
    raw_value = os.getenv("RUHU_DEV_AUTO_MIGRATE", "1").strip().lower()
    return raw_value not in {"0", "false", "no", "off"}


def _run_dev_database_migrations() -> None:
    if not _dev_auto_migrate_enabled():
        return
    seen_urls: set[str] = set()
    for env_name in ("RUHU_DATABASE_URL", "RUHU_AUTH_DATABASE_URL"):
        raw_database_url = os.getenv(env_name)
        if raw_database_url is None or not raw_database_url.strip():
            continue
        resolved_database_url = resolve_database_url(database_url=raw_database_url)
        if resolved_database_url in seen_urls:
            continue
        run_migrations(resolved_database_url)
        seen_urls.add(resolved_database_url)


def create_app():
    repo_root = Path(__file__).resolve().parents[2]
    _load_dev_environment(repo_root)
    _run_dev_database_migrations()
    # Enterprise posture: do NOT auto-bootstrap demo agents from examples/.
    # Agents are tenant-scoped resources created through the API or cloned
    # from templates. Tests can pass their own agent root explicitly.
    # RUHU_AGENT_ROOT env var allows opt-in to a specific directory for dev.
    configured_agent_root = os.getenv("RUHU_AGENT_ROOT")
    agent_root_path = Path(configured_agent_root) if configured_agent_root else repo_root / ".agent-root-empty"
    # Opt-in dev bootstrap: set RUHU_DEV_BOOTSTRAP_ORGANIZATION_ID to seed
    # the dev install under a single tenant (e.g. "dev").  When combined
    # with RUHU_KNOWLEDGE_* env vars, this gives a one-knob quickstart
    # without reintroducing the "public" sentinel fallback.
    bootstrap_organization_id = os.getenv("RUHU_DEV_BOOTSTRAP_ORGANIZATION_ID") or None
    return build_default_app(
        agent_root=agent_root_path,
        bootstrap_organization_id=bootstrap_organization_id,
    )

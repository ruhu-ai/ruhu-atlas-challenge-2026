from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from ruhu._dev_server import _load_dev_environment, _run_dev_database_migrations


def test_load_dev_environment_prefers_repo_env_before_override_file(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path
    (repo_root / ".env.development.local").write_text("ONLY_LOCAL=present\n", encoding="utf-8")
    (repo_root / ".env.development").write_text(
        "RUHU_LIVEKIT_SERVER_URL=ws://repo-livekit\nRUHU_DATABASE_URL=postgresql://repo\n",
        encoding="utf-8",
    )
    override_env = repo_root / "override.env"
    override_env.write_text(
        "RUHU_LIVEKIT_SERVER_URL=ws://override-livekit\nRUHU_LIVEKIT_API_KEY=override-key\nRUHU_DATABASE_URL=postgresql://override\n",
        encoding="utf-8",
    )

    with patch.dict(os.environ, {"RUHU_DEV_ENV_FILE": str(override_env)}, clear=True):
        _load_dev_environment(repo_root)

        assert "present" == os.environ["ONLY_LOCAL"]
        assert "ws://repo-livekit" == os.environ["RUHU_LIVEKIT_SERVER_URL"]
        assert "override-key" == os.environ["RUHU_LIVEKIT_API_KEY"]
        assert "postgresql://repo" == os.environ["RUHU_DATABASE_URL"]


def test_run_dev_database_migrations_uses_unique_resolved_urls() -> None:
    with patch.dict(
        os.environ,
        {
            "RUHU_DATABASE_URL": "postgresql://runtime",
            "RUHU_AUTH_DATABASE_URL": "postgres://runtime",
        },
        clear=True,
    ):
        with patch("ruhu._dev_server.run_migrations") as run_migrations:
            _run_dev_database_migrations()

    run_migrations.assert_called_once_with("postgresql+psycopg://runtime")


def test_run_dev_database_migrations_can_be_disabled() -> None:
    with patch.dict(
        os.environ,
        {
            "RUHU_DATABASE_URL": "postgresql://runtime",
            "RUHU_DEV_AUTO_MIGRATE": "0",
        },
        clear=True,
    ):
        with patch("ruhu._dev_server.run_migrations") as run_migrations:
            _run_dev_database_migrations()

    run_migrations.assert_not_called()

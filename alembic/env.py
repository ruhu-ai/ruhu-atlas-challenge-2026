from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection

from ruhu.db_models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Alembic's default ``alembic_version.version_num`` column is ``VARCHAR(32)``,
# which is too narrow for descriptive revision names like
# ``0043_realtime_idempotency_nullable_org`` (38 chars).  We widen or
# pre-create the column before migrations run so the update-version step that
# follows each migration never truncates.  Keeping this in env.py (rather
# than in a bootstrap migration) avoids the chicken-and-egg where the
# bootstrap itself could overflow the narrow column.
_VERSION_NUM_WIDTH = 255


def _ensure_wide_alembic_version_column(connection: Connection) -> None:
    """Create ``alembic_version`` with a wide ``version_num`` column, or
    widen an existing narrow column in place.  Idempotent, Postgres-only."""
    if connection.dialect.name != "postgresql":
        return
    exists = connection.execute(
        text("SELECT to_regclass('alembic_version')")
    ).scalar()
    if exists is None:
        connection.execute(
            text(
                f"CREATE TABLE alembic_version ("
                f"version_num VARCHAR({_VERSION_NUM_WIDTH}) NOT NULL, "
                f"CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        return
    current_width = connection.execute(
        text(
            "SELECT character_maximum_length "
            "FROM information_schema.columns "
            "WHERE table_name = 'alembic_version' AND column_name = 'version_num'"
        )
    ).scalar()
    if current_width is not None and current_width < _VERSION_NUM_WIDTH:
        connection.execute(
            text(
                f"ALTER TABLE alembic_version "
                f"ALTER COLUMN version_num TYPE VARCHAR({_VERSION_NUM_WIDTH})"
            )
        )


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    url = x_args.get("db_url") or os.getenv("RUHU_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("database URL is required for Alembic")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        with connection.begin():
            _ensure_wide_alembic_version_column(connection)

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

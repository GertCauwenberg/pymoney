"""Alembic environment.

Two settings here are not defaults and matter:

``render_as_batch``
    SQLite cannot ``ALTER TABLE`` in most of the ways a migration needs to.
    Batch mode rewrites the table instead, so one migration script works on both
    backends. Without it every schema change would need a SQLite special case.

``compare_type`` / ``compare_server_default``
    Autogenerate is otherwise blind to a column changing type, which is exactly
    the sort of change that silently corrupts a ledger.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from pymoney.store.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata


def _url() -> str:
    """Where to migrate.

    ``-x url=...`` wins, then ``$PYMONEY_DATABASE_URL``, then alembic.ini. No
    default: silently migrating the wrong database is worse than an error.

    Prefer the environment variable over putting the URL in alembic.ini. The ini
    file goes through configparser, so a ``%`` in a password or a query parameter
    has to be escaped as ``%%`` or the file will not parse.
    """
    cli = context.get_x_argument(as_dictionary=True).get("url")
    if cli:
        return cli
    env = os.environ.get("PYMONEY_DATABASE_URL")
    if env:
        return env
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    raise SystemExit("no database URL: set PYMONEY_DATABASE_URL or pass -x url=<dsn>")


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review or for a DBA."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

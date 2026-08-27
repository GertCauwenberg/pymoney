"""Apply the schema migrations from an installed package.

    python -m pymoney.store.migrate                 # upgrade $PYMONEY_DATABASE_URL to head
    python -m pymoney.store.migrate --url sqlite+pysqlite:///ledger.sqlite
    python -m pymoney.store.migrate --current       # just report the revision

``alembic upgrade head`` needs an ``alembic.ini`` in the working directory whose
``script_location`` points into the source tree. That works in a checkout and
nowhere else: a deployed image has the wheel and nothing more. So the runtime
builds the Alembic configuration itself, from the package it is running in, and
the only input is the database URL -- which is exactly what a container has.

Migrations are deliberately not run at server start-up. Two replicas starting at
once would race each other through the same DDL; a migration is a step of a
deployment, run once, by whoever is deploying.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from .engine import DEFAULT_URL_ENV, create_engine

__all__ = ["alembic_config", "current_revision", "head_revision", "main", "upgrade"]

SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    """An Alembic configuration pointing at this package's migrations."""
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    # The ini layer goes through configparser, which treats % as interpolation,
    # so a password or query parameter containing one has to be escaped.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade(url: str, revision: str = "head") -> None:
    """Bring the database at ``url`` up to ``revision``."""
    command.upgrade(alembic_config(url), revision)


def head_revision(url: str) -> str | None:
    """The newest migration this package knows about."""
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def current_revision(url: str) -> str | None:
    """The migration the database is at, or None for an unversioned one."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _resolve_url(explicit: str | None) -> str:
    url = explicit or os.environ.get(DEFAULT_URL_ENV)
    if not url:
        raise SystemExit(f"no database URL: set {DEFAULT_URL_ENV} or pass --url")
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pymoney.store.migrate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", help=f"SQLAlchemy URL; defaults to ${DEFAULT_URL_ENV}")
    parser.add_argument(
        "--revision", default="head", help="target revision (default: head)"
    )
    parser.add_argument(
        "--current",
        action="store_true",
        help="report the database's revision and the package's head, change nothing",
    )
    args = parser.parse_args(argv)
    url = _resolve_url(args.url)

    if args.current:
        print(f"database: {current_revision(url) or '(unversioned)'}")
        print(f"package:  {head_revision(url)}")
        return 0

    before = current_revision(url)
    upgrade(url, args.revision)
    after = current_revision(url)
    if before == after:
        print(f"already at {after}")
    else:
        print(f"upgraded {before or '(unversioned)'} -> {after}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

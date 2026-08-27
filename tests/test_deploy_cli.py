"""The command lines a deployment needs and the API does not provide.

``python -m pymoney.store.migrate`` applies the migrations from the installed
package -- there is no alembic.ini inside the image, and the documented
``alembic upgrade head`` silently did not work there. ``python -m
pymoney.store.grants`` lets a book created outside the API (the KMyMoney
importer, a script) be seen by someone.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select

from pymoney.core import AccountType, Commodity
from pymoney.importers.kmymoney.__main__ import main as import_main
from pymoney.store import (
    create_engine,
    create_schema,
    identity,
    metadata,
    migrate,
    open_book,
)
from pymoney.store import grants as grants_cli

FIXTURE = Path(__file__).parent / "fixtures" / "household.kmy.xml"


class TestMigrateCli:
    def test_upgrades_an_empty_database_from_another_directory(
        self, tmp_path, monkeypatch, capsys
    ):
        """No alembic.ini anywhere near the working directory, as in the image."""
        monkeypatch.chdir(tmp_path)
        url = f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite'}"
        assert migrate.main(["--url", url]) == 0
        out = capsys.readouterr().out
        assert out.startswith("upgraded (unversioned) ->")

        engine = create_engine(url)
        present = set(inspect(engine).get_table_names())
        assert {t.name for t in metadata.tables.values()} <= present
        engine.dispose()

    def test_reads_the_url_from_the_environment(self, tmp_path, monkeypatch):
        url = f"sqlite+pysqlite:///{tmp_path / 'env.sqlite'}"
        monkeypatch.setenv("PYMONEY_DATABASE_URL", url)
        assert migrate.main([]) == 0
        assert migrate.current_revision(url) == migrate.head_revision(url)

    def test_refuses_to_guess_the_database(self, monkeypatch):
        monkeypatch.delenv("PYMONEY_DATABASE_URL", raising=False)
        with pytest.raises(SystemExit, match="no database URL"):
            migrate.main([])

    def test_is_idempotent_and_reports_the_revision(self, tmp_path, capsys):
        url = f"sqlite+pysqlite:///{tmp_path / 'twice.sqlite'}"
        migrate.main(["--url", url])
        capsys.readouterr()
        assert migrate.main(["--url", url]) == 0
        assert capsys.readouterr().out.startswith("already at ")
        assert migrate.main(["--url", url, "--current"]) == 0
        out = capsys.readouterr().out
        assert "database: " in out and "package:  " in out

    def test_a_migrated_database_works_with_the_store(self, tmp_path):
        url = f"sqlite+pysqlite:///{tmp_path / 'use.sqlite'}"
        migrate.upgrade(url)
        engine = create_engine(url)
        book = open_book(engine, name="M", base_currency=Commodity.currency("EUR"))
        cash = book.add_account("Cash", AccountType.CASH)
        assert book.balance(cash.id).is_zero
        book.close()
        engine.dispose()


@pytest.fixture
def database(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'grants.sqlite'}"
    engine = create_engine(url)
    create_schema(engine)
    book = open_book(engine, name="G", base_currency=Commodity.currency("EUR"))
    book_id = book.id
    book.close()
    engine.dispose()
    return url, book_id


def _grants(url: str, book_id: UUID) -> dict[str, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(identity.book_grants.c.subject, identity.book_grants.c.role).where(
                    identity.book_grants.c.book_id == book_id
                )
            )
            return dict(rows.all())
    finally:
        engine.dispose()


class TestGrantsCli:
    def test_grant_list_and_revoke(self, database, capsys):
        url, book_id = database
        assert (
            grants_cli.main([str(book_id), "a@example.org", "owner", "--url", url]) == 0
        )
        assert (
            grants_cli.main([str(book_id), "b@example.org", "viewer", "--url", url]) == 0
        )
        assert _grants(url, book_id) == {
            "a@example.org": "owner",
            "b@example.org": "viewer",
        }

        # A second grant for the same subject replaces the role.
        assert (
            grants_cli.main([str(book_id), "b@example.org", "editor", "--url", url]) == 0
        )
        assert _grants(url, book_id)["b@example.org"] == "editor"

        capsys.readouterr()
        assert grants_cli.main(["--list", str(book_id), "--url", url]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "a@example.org: owner",
            "b@example.org: editor",
        ]

        assert (
            grants_cli.main([str(book_id), "b@example.org", "--revoke", "--url", url])
            == 0
        )
        assert "b@example.org" not in _grants(url, book_id)
        assert (
            grants_cli.main([str(book_id), "b@example.org", "--revoke", "--url", url])
            == 1
        )

    def test_a_bare_path_is_a_sqlite_file(self, database):
        url, book_id = database
        path = url.removeprefix("sqlite+pysqlite:///")
        assert (
            grants_cli.main([str(book_id), "c@example.org", "viewer", "--url", path]) == 0
        )
        assert _grants(url, book_id)["c@example.org"] == "viewer"

    def test_an_unknown_book_is_an_error(self, database, capsys):
        url, _ = database
        assert (
            grants_cli.main([str(uuid4()), "a@example.org", "owner", "--url", url]) == 2
        )
        assert "no book" in capsys.readouterr().err

    def test_the_arguments_are_checked(self, database):
        url, book_id = database
        with pytest.raises(SystemExit):
            grants_cli.main([str(book_id), "--url", url])  # no subject
        with pytest.raises(SystemExit):
            grants_cli.main([str(book_id), "a@example.org", "--url", url])  # no role
        with pytest.raises(SystemExit):
            grants_cli.main([str(book_id), "a@example.org", "god", "--url", url])

    def test_the_helper_validates_its_inputs(self, database):
        url, book_id = database
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                with pytest.raises(ValueError, match="role"):
                    grants_cli.grant(conn, book_id, "x@example.org", "god")
                with pytest.raises(ValueError, match="subject"):
                    grants_cli.grant(conn, book_id, "  ", "owner")
        finally:
            engine.dispose()


class TestImporterGrantsAnOwner:
    @pytest.fixture
    def kmy(self, tmp_path: Path) -> Path:
        target = tmp_path / "household.kmy"
        target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
        return target

    def test_owner_is_granted(self, kmy, tmp_path):
        target = tmp_path / "ledger.sqlite"
        assert (
            import_main(
                [str(kmy), "--database", str(target), "--owner", "me@example.org", "-q"]
            )
            == 0
        )
        url = f"sqlite+pysqlite:///{target}"
        engine = create_engine(url)
        book = open_book(engine)
        book_id = book.id
        book.close()
        engine.dispose()
        assert _grants(url, book_id) == {"me@example.org": "owner"}

    def test_owner_needs_a_database(self, kmy):
        with pytest.raises(SystemExit, match="--owner needs --database"):
            import_main([str(kmy), "--dry-run", "--owner", "me@example.org"])

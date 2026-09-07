"""Uploading a KMyMoney file makes a book the uploader owns."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from tests.conftest_api import Client

FIXTURE = Path(__file__).parent / "fixtures" / "household.kmy.xml"


@pytest.fixture
def kmy(tmp_path: Path) -> Path:
    target = tmp_path / "household.kmy"
    target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    return target


def upload(client: Client, path: Path, **fields):
    with path.open("rb") as handle:
        return client.http.post(
            "/books/import/kmymoney",
            files={"file": (path.name, handle, "application/octet-stream")},
            data={"name": "From KMyMoney", **fields},
            headers=client.headers,
        )


class TestImportUpload:
    def test_the_uploader_owns_the_new_book_and_gets_the_report(self, user: Client, kmy):
        response = upload(user, kmy)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["book"]["name"] == "From KMyMoney" and body["book"]["role"] == "owner"
        assert body["ok"] is True
        assert body["imported"]["transactions"] == 15
        assert body["imported"]["accounts"] >= 12
        assert body["imported"]["schedules"] == 2
        assert (
            "rounding adjustments" in body["imported"]
            or body["adjustments"] == []
            or body["adjustments"]
        )
        assert "Imported" in body["summary"]

        book_id = body["book"]["id"]
        books = user.get("/books").json()
        assert [(b["name"], b["role"]) for b in books] == [("From KMyMoney", "owner")]
        accounts = user.get(f"/books/{book_id}/accounts").json()
        current = next(a for a in accounts if a["path"] == "Assets:Current Account")
        user.book_id = book_id
        assert user.balance(current["id"])["minor"] == 800773
        assert user.get(f"/books/{book_id}").json()["base_currency_id"] is not None
        assert user.get(f"/books/{book_id}/integrity").json() == []

    def test_plain_xml_works_too_and_options_are_honoured(self, user: Client, kmy):
        response = upload(user, FIXTURE, rounding_adjustments="false", skip_prices="true")
        assert response.status_code == 201, response.text
        body = response.json()
        assert "prices" not in body["imported"]
        assert "rounding adjustments" not in body["imported"]

    def test_a_broken_file_leaves_no_book_behind(self, user: Client, tmp_path):
        bad = tmp_path / "bad.kmy"
        bad.write_bytes(b"this is not xml at all")
        response = upload(user, bad)
        assert response.status_code == 422
        assert "could not be imported" in response.json()["detail"]
        assert user.get("/books").json() == []

    def test_any_failure_leaves_no_book_behind(self, user: Client, kmy, monkeypatch):
        """Not only a bad file: a bug in the importer, a disk error, anything.
        Before, only three exception types were cleaned up after; everything
        else escaped between creating the book and granting the owner, leaving
        a book nobody could see or delete."""
        from pymoney.api.routers import imports
        from pymoney.store import schema

        def exploding(*args, **kwargs):
            raise OSError("disk on fire")

        monkeypatch.setattr(imports, "import_kmymoney", exploding)
        with pytest.raises(OSError):
            upload(user, kmy)
        assert user.get("/books").json() == []
        # Not merely hidden behind a missing grant: the rows are gone.
        from sqlalchemy import func, select

        with user.http.app.state.engine.connect() as conn:
            assert (
                conn.execute(select(func.count()).select_from(schema.books)).scalar_one()
                == 0
            )
        assert user.http.app.state.registry.known_ids() == ()

    def test_the_owner_grant_is_written_with_the_book(
        self, user: Client, kmy, monkeypatch
    ):
        """The grant is part of creating the book, not a step after importing:
        should the cleanup itself never run (the process killed mid-import),
        what is left is a book its owner can see and delete."""
        from pymoney.api.routers import imports
        from pymoney.store.grants import grants_for

        engine = user.http.app.state.engine
        seen: list[list[tuple[str, str]]] = []

        def spying(path, book, options=None):
            # At import time -- right after create() -- the grant already exists.
            with engine.connect() as conn:
                seen.append(grants_for(conn, book.id))
            raise RuntimeError("killed mid-import")

        monkeypatch.setattr(imports, "import_kmymoney", spying)
        with pytest.raises(RuntimeError):
            upload(user, kmy)
        assert seen == [[("owner@example.invalid", "owner")]]

    def test_a_gzip_bomb_is_refused(self, user: Client, tmp_path):
        user.http.app.state.settings.max_upload_mb = 1
        filler = (
            b'<?xml version="1.0"?><KMYMONEY-FILE><!-- ' + b"0" * (64 * 1024**2) + b" -->"
        )
        bomb = tmp_path / "bomb.kmy"
        bomb.write_bytes(gzip.compress(filler, compresslevel=1))
        assert bomb.stat().st_size < 1024**2
        response = upload(user, bomb)
        assert response.status_code == 422
        assert "inflates past" in response.json()["detail"]
        assert user.get("/books").json() == []

    def test_hostile_xml_is_refused(self, user: Client, tmp_path):
        evil = tmp_path / "evil.kmy"
        evil.write_text(
            '<?xml version="1.0"?><!DOCTYPE k [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            '<KMYMONEY-FILE><ACCOUNTS><ACCOUNT id="A" name="&x;"/></ACCOUNTS></KMYMONEY-FILE>'
        )
        response = upload(user, evil)
        assert response.status_code == 422
        assert "never needs" in response.json()["detail"]
        assert user.get("/books").json() == []

    def test_too_large_is_refused_early(self, user: Client, tmp_path, monkeypatch):
        user.http.app.state.settings.max_upload_mb = 1
        big = tmp_path / "big.kmy"
        big.write_bytes(b"x" * (1024 * 1024 + 1))
        assert upload(user, big).status_code == 413
        assert user.get("/books").json() == []

    def test_needs_a_session(self, api: Client, kmy):
        assert upload(api, kmy).status_code == 401

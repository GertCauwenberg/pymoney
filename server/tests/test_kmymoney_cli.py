"""The command line: ``python -m pymoney.importers.kmymoney``."""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from pymoney.importers.kmymoney.__main__ import main
from pymoney.store import create_engine, open_book

FIXTURE = Path(__file__).parent / "fixtures" / "household.kmy.xml"


@pytest.fixture
def kmy(tmp_path: Path) -> Path:
    """A gzipped copy, as KMyMoney actually writes them."""
    target = tmp_path / "household.kmy"
    target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    return target


class TestInspect:
    def test_describes_the_file_without_importing(self, kmy, capsys):
        assert main([str(kmy), "--inspect"]) == 0
        out = capsys.readouterr().out
        assert "base currency: EUR" in out
        assert "accounts:      12" in out
        assert "securities:    1" in out
        assert "transactions:  15 (as declared)" in out

    def test_names_what_the_file_holds_and_where_it_goes(self, kmy, capsys):
        main([str(kmy), "--inspect"])
        out = capsys.readouterr().out
        # Schedules and budgets are read now.  Only saved reports and cost
        # centres still have no home in PyMoney, and this file holds none.
        assert "schedules:     2 (imported)" in out
        assert "budgets:       1 (imported)" in out
        assert "not imported" not in out

    def test_needs_no_database(self, kmy):
        """Inspecting is safe and side-effect free."""
        assert main([str(kmy), "--inspect"]) == 0


class TestDryRun:
    def test_reports_without_a_database(self, kmy, capsys):
        assert main([str(kmy), "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "Dry run of" in out
        assert "result: OK" in out

    def test_shows_the_rounding_adjustments(self, kmy, capsys):
        """The point of a dry run: see the corrections before committing."""
        main([str(kmy), "--dry-run"])
        out = capsys.readouterr().out
        assert "rounding adjustments: 2 account(s)" in out
        assert "Assets:Dollar Wallet" in out

    def test_verbose_adds_the_notes(self, kmy, capsys):
        main([str(kmy), "--dry-run", "--verbose"])
        assert "certificate of deposit" in capsys.readouterr().out

    def test_leaves_no_database_behind(self, kmy, tmp_path):
        main([str(kmy), "--dry-run"])
        assert not list(tmp_path.glob("*.sqlite"))


class TestImport:
    def test_into_a_sqlite_path(self, kmy, tmp_path, capsys):
        target = tmp_path / "ledger.sqlite"
        assert main([str(kmy), "--database", str(target), "--name", "Household"]) == 0
        assert "Imported" in capsys.readouterr().out
        assert target.exists()

        engine = create_engine(f"sqlite+pysqlite:///{target}")
        book = open_book(engine)
        assert book.name == "Household"
        current = book.find_account("Assets:Current Account")
        assert current is not None
        assert book.balance(current.id).minor == 800773
        wallet = book.find_account("Assets:Dollar Wallet")
        assert book.balance(wallet.id).minor == 5100
        assert book.check_integrity() == ()
        book.close()
        engine.dispose()

    def test_a_full_sqlalchemy_url_also_works(self, kmy, tmp_path):
        target = tmp_path / "url.sqlite"
        assert main([str(kmy), "--database", f"sqlite+pysqlite:///{target}"]) == 0
        assert target.exists()

    def test_quiet_prints_nothing_when_it_works(self, kmy, tmp_path, capsys):
        target = tmp_path / "quiet.sqlite"
        assert main([str(kmy), "--database", str(target), "--quiet"]) == 0
        assert capsys.readouterr().out == ""

    def test_rounding_adjustments_can_be_declined(self, kmy, tmp_path):
        target = tmp_path / "raw.sqlite"
        assert (
            main(
                [
                    str(kmy),
                    "--database",
                    str(target),
                    "--no-rounding-adjustments",
                ]
            )
            == 0
        )
        engine = create_engine(f"sqlite+pysqlite:///{target}")
        book = open_book(engine)
        wallet = book.find_account("Assets:Dollar Wallet")
        assert book.balance(wallet.id).minor == 5099
        book.close()
        engine.dispose()

    def test_prices_can_be_skipped(self, kmy, tmp_path):
        target = tmp_path / "noprices.sqlite"
        assert main([str(kmy), "--database", str(target), "--skip-prices"]) == 0
        engine = create_engine(f"sqlite+pysqlite:///{target}")
        book = open_book(engine)
        assert len(book.prices) == 0
        book.close()
        engine.dispose()

    def test_a_second_run_fails_cleanly(self, kmy, tmp_path, capsys):
        target = tmp_path / "twice.sqlite"
        assert main([str(kmy), "--database", str(target)]) == 0
        capsys.readouterr()
        with pytest.raises(Exception, match="already holds transactions"):
            main([str(kmy), "--database", str(target)])


class TestErrors:
    def test_a_missing_file(self, tmp_path, capsys):
        assert main([str(tmp_path / "nope.kmy"), "--dry-run"]) == 2
        assert "no such file" in capsys.readouterr().err

    def test_an_encrypted_file(self, tmp_path, capsys):
        path = tmp_path / "secret.kmy"
        path.write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
        assert main([str(path), "--dry-run"]) == 2
        assert "gpg --decrypt" in capsys.readouterr().err

    def test_a_file_that_is_not_kmymoney(self, tmp_path, capsys):
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0JFIF")
        assert main([str(path), "--dry-run"]) == 2
        assert "cannot read" in capsys.readouterr().err

    def test_no_target_and_no_dry_run(self, kmy):
        with pytest.raises(SystemExit, match="--database is required"):
            main([str(kmy)])

    def test_a_failed_import_exits_nonzero(self, tmp_path, capsys):
        """A report with errors must not look like success to a shell script."""
        path = tmp_path / "broken.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Cash" type="3" '
            'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
            '<TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
            '<SPLIT account="GONE" shares="100/100" value="100/100"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        assert main([str(path), "--dry-run"]) == 1
        assert "result: FAILED" in capsys.readouterr().out

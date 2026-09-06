"""The CSV statement import dialog, offscreen, against the real API.

The same shape as ``test_desktop_ui``: synchronous executor, so every server
call finishes before the next assertion, and the modal file picker is replaced
rather than clicked.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.testing import PASSWORD, Client
from PySide6.QtWidgets import QComboBox, QFileDialog

from pymoney_desktop import OfflineClient
from pymoney_desktop.ui.import_dialog import ImportStatementDialog
from pymoney_desktop.ui.main_window import MainWindow, SyncExecutor
from tests.test_desktop_clients import EMAIL, InProcessTransport
from tests.test_desktop_ui import select_account

CSV = (
    b"Date,Details,Amount\n"
    b"2026-02-03,TESCO STORES 7 LEEDS,-42.11\n"
    b"2026-02-04,TRANSFER TO SAVINGS,-200.00\n"
    b"2026-02-05,A CAFE NOBODY KNOWS,-3.80\n"
)


@pytest.fixture
def window(api: Client, tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    boxes: list[tuple[str, str]] = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(
                lambda parent, title, text, *a, **k: boxes.append((title, text))
            ),
        )
    api.register(EMAIL)
    book_id = api.make_book("Desk")
    checking = api.account("Checking", "CHECKING")
    savings = api.account("Savings", "SAVINGS")
    groceries = api.account("Groceries", "EXPENSE")
    opening = api.account("Opening", "EQUITY")
    api.account("Uncategorised", "EXPENSE")
    api.post(
        f"/books/{book_id}/payees",
        json={
            "name": "Tesco",
            "match_keys": ["TESCO STORES"],
            "default_account_id": groceries["id"],
        },
    )
    # Savings already shows the +200; Checking's side of it has never been
    # entered, so the CSV's "-200 to savings" row is the missing far leg --
    # a transfer, defaulting to skipped so the money is not counted twice.
    api.post_transaction(
        "2026-02-04",
        [
            {"account_id": savings["id"], "value": "200.00"},
            {"account_id": opening["id"]},
        ],
        memo="showed up on the savings statement first",
    )
    api.logout()

    transport = InProcessTransport(api.http)
    client = OfflineClient(transport, tmp_path / "replica.sqlite")
    client.login(EMAIL, PASSWORD)
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    win.report_error = lambda title, message: win.__setattr__(
        "reported", (title, message)
    )  # type: ignore[method-assign]
    qtbot.addWidget(win)
    win.boxes = boxes  # type: ignore[attr-defined]
    win.load()
    yield win, api, UUID(book_id), UUID(checking["id"]), UUID(groceries["id"])
    client.close()


def _file(monkeypatch, tmp_path, content=CSV):
    path = tmp_path / "statement.csv"
    path.write_bytes(content)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(path), "CSV files (*.csv)")),
    )


def _row(dialog: ImportStatementDialog, description: str) -> int:
    for i, proposal in enumerate(dialog.preview.proposals):
        if proposal.description == description:
            return i
    raise AssertionError(f"no row {description!r}")


class TestImportDialog:
    def test_file_to_posted_transactions(self, window, tmp_path, monkeypatch):
        win, _api, book_id, checking, groceries = window
        select_account(win, "Checking")
        assert win.import_button.isEnabled()
        _file(monkeypatch, tmp_path)

        def drive(dialog: ImportStatementDialog) -> int:
            dialog._next()  # pick the file, inspect it, land on the mapping page
            assert dialog.map_page.date.currentData() == "Date"
            assert dialog.map_page.amount.currentData() == "Amount"
            dialog._next()  # preview

            review = dialog.review_page
            assert {p.status for p in review.proposals} == {"new", "transfer"}
            tesco = review.row_widgets[_row(dialog, "TESCO STORES 7 LEEDS")]
            assert tesco["payee"].currentText() == "Tesco"
            assert tesco["category"].currentText() == "Groceries"
            transfer_row = _row(dialog, "TRANSFER TO SAVINGS")
            assert review.proposals[transfer_row].existing_account_path == "Savings"
            transfer = review.row_widgets[transfer_row]
            assert not transfer["post"].isChecked()  # its far side is on Savings already

            cafe = review.row_widgets[_row(dialog, "A CAFE NOBODY KNOWS")]
            assert cafe["payee"].currentText()  # a cleaned-up name, offered
            assert review.needs_fallback()  # the cafe has no category yet
            category = cafe["category"]
            assert isinstance(category, QComboBox)
            category.setCurrentIndex(category.findData(str(groceries)))
            assert not review.needs_fallback()

            assert dialog.next_button.text() == "Import 2 transaction(s)"
            dialog._next()  # commit

            assert dialog.report is not None and dialog.report.posted == 2
            assert dialog.report.skipped_transfers == 1
            assert dialog.report_page.heading.text() == "Imported"
            dialog.accept()
            return dialog.result()

        win.run_dialog = drive  # type: ignore[method-assign]
        win.import_statement()
        assert getattr(win, "reported", None) is None
        assert win.boxes == []

        # Checking shows the two new rows; the transfer was not re-posted.
        assert win.register.rowCount() == 2
        amounts = {win.register.item(r, 5).text() for r in range(win.register.rowCount())}
        assert amounts == {"-42.11", "-3.80"}
        # The new payee was created and the column mapping remembered.
        names = {p.name for p in win.payees}
        assert "Tesco" in names and len(names) == 2
        assert win.client.import_mapping(book_id, checking).date == "Date"

    def test_a_duplicate_import_posts_nothing(self, window, tmp_path, monkeypatch):
        win, _api, _book_id, _checking, groceries = window
        select_account(win, "Checking")
        _file(monkeypatch, tmp_path)

        def first(dialog: ImportStatementDialog) -> int:
            dialog._next()
            dialog._next()
            cafe = dialog.review_page.row_widgets[_row(dialog, "A CAFE NOBODY KNOWS")]
            cafe["category"].setCurrentIndex(cafe["category"].findData(str(groceries)))
            dialog._next()
            dialog.accept()
            return dialog.result()

        win.run_dialog = first  # type: ignore[method-assign]
        win.import_statement()
        posted_rows = win.register.rowCount()

        def again(dialog: ImportStatementDialog) -> int:
            dialog._next()
            dialog._next()
            # The two I posted are duplicates; the transfer is still one-sided.
            assert {p.status for p in dialog.review_page.proposals} == {
                "duplicate",
                "transfer",
            }
            assert all(not w["post"].isChecked() for w in dialog.review_page.row_widgets)
            assert dialog.next_button.text() == "Nothing to import"
            assert not dialog.next_button.isEnabled()
            dialog.accept()
            return dialog.result()

        win.run_dialog = again  # type: ignore[method-assign]
        win.import_statement()
        assert win.register.rowCount() == posted_rows  # unchanged

    def test_a_bad_mapping_keeps_you_on_the_mapping_page(
        self, window, tmp_path, monkeypatch
    ):
        win, *_ = window
        select_account(win, "Checking")
        _file(
            monkeypatch,
            tmp_path,
            b"Datum;Omschrijving;Bedrag\n05-02-2026;SHOP;-18,40\n",
        )

        def drive(dialog: ImportStatementDialog) -> int:
            dialog._next()  # inspect -> mapping (guesses ';' and ',')
            dialog.map_page.decimal.setCurrentIndex(dialog.map_page.decimal.findData("."))
            dialog._next()  # preview is refused
            assert dialog.pages.currentIndex() == 0  # still on the mapping page
            assert "decimal" in dialog.status.text().lower() or "separator" in (
                dialog.status.text().lower()
            )
            dialog.reject()
            return dialog.result()

        win.run_dialog = drive  # type: ignore[method-assign]
        win.import_statement()
        assert win.register.rowCount() == 0  # nothing was posted to Checking

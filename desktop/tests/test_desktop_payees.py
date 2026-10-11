"""The desktop payees dialog, offscreen, against the real API.

The dialog is driven the way the windows are: a synchronous executor, and the
form answered through the dialog's ``run_dialog`` hook rather than by clicking.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.schemas import PayeeIn, PayeePatch, TransactionIn
from pymoney.api.testing import PASSWORD, Client
from PySide6.QtWidgets import QDialog, QMessageBox

from pymoney_desktop import OfflineClient
from pymoney_desktop.ui.payees_dialog import PayeeForm, PayeesDialog
from pymoney_desktop.ui.ui_utils import SyncExecutor
from tests.test_desktop_clients import EMAIL, InProcessTransport


class Table:
    """The dialog's table, read the way a person reads it."""

    def __init__(self, dialog: PayeesDialog) -> None:
        self.dialog = dialog

    def rows(self) -> list[tuple[str, str, str, str]]:
        table = self.dialog.table
        out = []
        for row in range(table.rowCount()):
            out.append(tuple(table.item(row, col).text() for col in range(4)))
        return out

    def names(self) -> list[str]:
        return [name for name, *_ in self.rows()]

    def select(self, name: str) -> None:
        for row, (row_name, *_rest) in enumerate(self.rows()):
            if row_name == name:
                self.dialog.table.setCurrentCell(row, 0)
                return
        raise AssertionError(f"no row {name!r} in {self.rows()}")


@pytest.fixture
def payees(api: Client, tmp_path, qtbot, monkeypatch):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    api.register(EMAIL)
    book_id = UUID(api.make_book("Desk"))
    checking = UUID(api.account("Checking", "CHECKING")["id"])
    food = UUID(api.account("Food", "EXPENSE")["id"])
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "replica.sqlite")
    client.login(EMAIL, PASSWORD)
    yield client, book_id, checking, food
    client.close()


def open_dialog(
    client,
    book_id,
    qtbot,
    *,
    can_edit: bool = True,
    on_show_transactions=None,
) -> PayeesDialog:
    dialog = PayeesDialog(
        client,
        book_id,
        client.accounts(book_id),
        can_edit=can_edit,
        on_show_transactions=on_show_transactions,
        executor=SyncExecutor(),
    )
    qtbot.addWidget(dialog)
    return dialog


def fill(form: PayeeForm, **fields: object) -> None:
    """Answer the form the way a person would, then press Save."""
    for key, value in fields.items():
        widget = getattr(form, key)
        if hasattr(widget, "setCurrentIndex") and isinstance(value, str):
            widget.setCurrentIndex(widget.findData(value))
        else:
            widget.setText(str(value))
    form._save()


# -- reading the list -------------------------------------------------------
def test_the_list_is_alphabetical_and_shows_the_whole_payee(payees, qtbot):
    client, book_id, checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Bakery"))
    client.create_payee(
        book_id,
        PayeeIn(
            name="Acme",
            notes="Wholesale",
            match_keys=["ACME", "Acme BV"],
            default_account_id=checking,
        ),
    )
    dialog = open_dialog(client, book_id, qtbot)
    table = Table(dialog)

    assert table.names() == ["Acme", "Bakery"]
    assert table.rows()[0] == (
        "Acme",
        "ACME, Acme BV",
        "Checking",
        "Wholesale",
    )


def test_the_filter_matches_names_and_spellings(payees, qtbot):
    client, book_id, _checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Acme Groceries"))
    client.create_payee(book_id, PayeeIn(name="Bakery", match_keys=["The Bread Shop"]))
    dialog = open_dialog(client, book_id, qtbot)
    table = Table(dialog)

    dialog.filter.setText("acme")
    assert table.names() == ["Acme Groceries"]

    # A spelling finds the payee just as well as the name does.
    dialog.filter.setText("bread")
    assert table.names() == ["Bakery"]

    dialog.filter.setText("nothing here")
    assert table.names() == []
    assert "Nothing matches." in dialog.empty.text()

    dialog.filter.setText("")
    assert table.names() == ["Acme Groceries", "Bakery"]


def test_an_empty_book_says_where_payees_come_from(payees, qtbot):
    client, book_id, _checking, _food = payees
    dialog = open_dialog(client, book_id, qtbot)
    assert Table(dialog).names() == []
    assert "No payees yet" in dialog.empty.text()


def test_the_buttons_are_for_writers_alone(payees, qtbot):
    client, book_id, _checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Acme"))

    reader = open_dialog(client, book_id, qtbot, can_edit=False)
    assert not reader.add_button.isEnabled()
    assert not reader.edit_button.isEnabled()
    assert not reader.delete_button.isEnabled()
    assert "Editors and owners" in reader.blurb.text()
    Table(reader).select("Acme")
    assert not reader.edit_button.isEnabled()

    writer = open_dialog(client, book_id, qtbot)
    assert writer.add_button.isEnabled()
    # Nothing to edit until one is picked.
    assert not writer.edit_button.isEnabled()
    assert not writer.transactions_button.isEnabled()
    Table(writer).select("Acme")
    assert writer.edit_button.isEnabled() and writer.delete_button.isEnabled()
    assert "no transaction names" in writer.blurb.text()


def test_the_transactions_button_opens_the_search_for_that_payee(payees, qtbot):
    client, book_id, _checking, _food = payees
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    seen: list[UUID] = []

    def dummy(payee_id, parent):
        seen.append(payee_id)

    dialog = open_dialog(client, book_id, qtbot, on_show_transactions=dummy)
    Table(dialog).select("Acme")
    dialog.show_transactions()
    assert seen == [acme.id]


# -- writing ----------------------------------------------------------------
def test_adding_a_payee(payees, qtbot):
    client, book_id, checking, _food = payees
    dialog = open_dialog(client, book_id, qtbot)

    def run(form: PayeeForm):
        fill(
            form,
            name="Acme",
            notes="Wholesale",
            address="Main Street 1",
            match_keys="ACME, Acme BV",
            default_account=str(checking),
        )
        return form.result()

    dialog.run_dialog = run
    dialog.add_payee()

    saved = next(p for p in client.payees(book_id) if p.name == "Acme")
    assert saved.notes == "Wholesale"
    assert saved.address == "Main Street 1"
    assert saved.match_keys == ["ACME", "Acme BV"]
    assert saved.default_account_id == checking
    assert dialog.changed
    assert ("Acme", "ACME, Acme BV", "Checking", "Wholesale") in Table(dialog).rows()


def test_editing_sends_only_what_changed(payees, qtbot, monkeypatch):
    client, book_id, _checking, _food = payees
    acme = client.create_payee(
        book_id, PayeeIn(name="Acme", notes="Wholesale", match_keys=["ACME"])
    )
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")

    sent: list[PayeePatch] = []
    original = client.update_payee

    def spy(book_id, payee_id, body):
        sent.append(body)
        return original(book_id, payee_id, body)

    monkeypatch.setattr(client, "update_payee", spy)

    def run(form: PayeeForm):
        fill(form, name="Acme", notes="Wholesale", address="Main Street 1")
        return form.result()

    dialog.run_dialog = run
    dialog.edit_payee()

    assert len(sent) == 1
    # Only the address was touched, so only the address travelled.
    assert sent[0].model_dump(exclude_unset=True) == {"address": "Main Street 1"}
    saved = next(p for p in client.payees(book_id) if p.id == acme.id)
    assert (saved.name, saved.notes, saved.address) == (
        "Acme",
        "Wholesale",
        "Main Street 1",
    )
    assert dialog.changed


def test_editing_nothing_sends_nothing(payees, qtbot, monkeypatch):
    client, book_id, _checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Acme", notes="Wholesale"))
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")

    def boom(book_id, payee_id, body):
        raise AssertionError("nothing should be sent")

    monkeypatch.setattr(client, "update_payee", boom)

    def run(form: PayeeForm):
        fill(form, name="Acme", notes="Wholesale")
        return form.result()

    dialog.run_dialog = run
    dialog.edit_payee()
    assert not dialog.changed


def test_deleting_an_unused_payee(payees, qtbot):
    client, book_id, _checking, _food = payees
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")
    dialog.delete_payee()

    assert all(p.id != acme.id for p in client.payees(book_id))
    assert dialog.changed
    assert Table(dialog).names() == []


def test_a_refusal_is_shown_and_the_list_re_read(payees, qtbot):
    client, book_id, checking, food = payees
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    client.post_transaction(
        book_id,
        TransactionIn.model_validate(
            {
                "date": "2026-02-01",
                "legs": [
                    {"account_id": str(checking), "value": "-10.00"},
                    {"account_id": str(food)},
                ],
                "payee_id": str(acme.id),
            }
        ),
    )
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")
    dialog.delete_payee()
    assert "Could not delete" in dialog.status.text()
    assert "transaction" in dialog.status.text()
    assert any(p.id == acme.id for p in client.payees(book_id))


# -- the form's own rules ---------------------------------------------------
def test_a_payee_with_the_same_name_is_refused_before_the_server_says_so(payees, qtbot):
    client, book_id, _checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Acme"))
    bakery = client.create_payee(book_id, PayeeIn(name="Bakery"))

    form = PayeeForm(client.payees(book_id), client.accounts(book_id), existing=bakery)
    qtbot.addWidget(form)
    form.name.setText("acme")  # the same name, only the case differs
    assert not form.save.isEnabled()
    assert "already exists" in form.hint.text()

    form.name.setText("Bakery on the corner")
    assert form.save.isEnabled()
    assert form.hint.text() == ""


def test_a_new_payee_may_not_take_a_name_that_exists(payees, qtbot):
    client, book_id, _checking, _food = payees
    client.create_payee(book_id, PayeeIn(name="Acme"))
    form = PayeeForm(client.payees(book_id), client.accounts(book_id))
    qtbot.addWidget(form)
    assert not form.save.isEnabled()  # no name yet
    form.name.setText("Acme")
    assert not form.save.isEnabled()
    assert "already exists" in form.hint.text()
    form.name.setText("Acme Groceries")
    assert form.save.isEnabled()


def test_merging_one_payee_into_another(payees, qtbot):
    client, book_id, _checking, _food = payees
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    bakery = client.create_payee(
        book_id, PayeeIn(name="Bakery", match_keys=["The Bread Shop"])
    )
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")

    def run(form: PayeeForm):
        assert form.merge_target.count() > 1
        form.merge_target.setCurrentIndex(1)
        form._ask_merge()
        assert "There is no undo" in form.merge_confirm.text()
        form._do_merge()
        return QDialog.DialogCode.Accepted

    dialog.run_dialog = run
    dialog.edit_payee()

    # The source is gone, its spellings kept on the target.
    survivors = {p.name: p for p in client.payees(book_id)}
    assert set(survivors) == {"Bakery"}
    assert "Acme" in survivors["Bakery"].match_keys
    assert "The Bread Shop" in survivors["Bakery"].match_keys
    assert all(p.id != acme.id for p in client.payees(book_id))
    assert Table(dialog).names() == ["Bakery"]
    assert bakery is not None


def test_a_lone_payee_has_nothing_to_merge_into(payees, qtbot):
    client, book_id, _checking, _food = payees
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    dialog = open_dialog(client, book_id, qtbot)
    Table(dialog).select("Acme")

    def run(form: PayeeForm):
        assert not hasattr(form, "merge_target")
        return QDialog.DialogCode.Rejected

    dialog.run_dialog = run
    dialog.edit_payee()
    assert any(p.id == acme.id for p in client.payees(book_id))


# -- opened from the window -------------------------------------------------
@pytest.fixture
def window(payees, qtbot):
    """The main window on the same book, driven synchronously."""
    from pymoney_desktop.ui.main_window import MainWindow

    client, book_id, checking, food = payees
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    qtbot.addWidget(win)
    win.load()
    yield win, client, book_id, checking, food


def test_settings_offers_the_payees(window):
    win, *_ = window
    settings = next(
        a.menu() for a in win.menuBar().actions() if a.text().endswith("Settings")
    )
    assert settings is not None
    assert "Payees…" in [a.text() for a in settings.actions()]


def test_opening_it_from_the_window_changes_the_payees(window):
    win, client, book_id, _checking, _food = window
    client.create_payee(book_id, PayeeIn(name="Acme", notes="Wholesale"))

    def run(dialog):
        if isinstance(dialog, PayeesDialog):
            assert dialog.can_edit  # the window's user may write
            Table(dialog).select("Acme")

            def answer(form: PayeeForm):
                fill(form, name="Acme Groceries")
                return form.result()

            dialog.run_dialog = answer
            dialog.edit_payee()
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Accepted

    win.run_dialog = run  # type: ignore[method-assign]
    win.open_payees()

    assert any(p.name == "Acme Groceries" for p in client.payees(book_id))


def test_the_transactions_button_opens_the_search_dialog_for_that_payee(window):
    win, client, book_id, checking, food = window
    acme = client.create_payee(book_id, PayeeIn(name="Acme"))
    client.post_transaction(
        book_id,
        TransactionIn.model_validate(
            {
                "date": "2026-02-01",
                "legs": [
                    {"account_id": str(checking), "value": "-10.00"},
                    {"account_id": str(food)},
                ],
                "payee_id": str(acme.id),
            }
        ),
    )

    def run(dialog):
        if isinstance(dialog, PayeesDialog):
            Table(dialog).select("Acme")
            dialog.show_transactions()
        return QDialog.DialogCode.Accepted

    win.load()
    win.run_dialog = run  # type: ignore[method-assign]
    win.open_payees()

    search = win.search_dialog
    assert search is not None
    assert search.isVisible()
    assert search.payee.currentData() == str(acme.id)
    assert [t.payee_id for t in search.results] == [acme.id]


def test_a_viewer_gets_a_read_only_list(window):
    win, *_ = window
    win.book = win.book.model_copy(update={"role": "viewer"})

    def run(dialog):
        if isinstance(dialog, PayeesDialog):
            assert not dialog.can_edit
            assert not dialog.add_button.isEnabled()
        return QDialog.DialogCode.Accepted

    win.run_dialog = run  # type: ignore[method-assign]
    win.open_payees()

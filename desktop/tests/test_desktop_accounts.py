"""The desktop chart of accounts, offscreen, against the real API.

The dialog is driven the way the window's are: a synchronous executor, and the
form answered through the dialog's ``run_dialog`` hook rather than by clicking.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.schemas import AccountIn, TransactionIn
from pymoney.api.testing import PASSWORD, Client
from PySide6.QtWidgets import QDialog, QMessageBox

from pymoney_desktop import OfflineClient
from pymoney_desktop.ui.accounts_dialog import AccountForm, AccountsDialog
from pymoney_desktop.ui.main_window import SyncExecutor
from tests.test_desktop_clients import EMAIL, InProcessTransport
from tests.test_desktop_ui import tree_texts


class Tree:
    """The dialog's tree, read the way a person reads it."""

    def __init__(self, dialog: AccountsDialog) -> None:
        self.dialog = dialog

    def rows(self) -> list[tuple[int, str, str, str]]:
        out: list[tuple[int, str, str, str]] = []

        def walk(item: Any, depth: int) -> None:
            out.append((depth, item.text(0), item.text(1), item.text(2)))
            for i in range(item.childCount()):
                walk(item.child(i), depth + 1)

        root = self.dialog.tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i), 0)
        return out

    def roots(self) -> list[str]:
        return [name for depth, name, _, _ in self.rows() if depth == 0]

    def select(self, name: str) -> None:
        def find(item: Any) -> Any:
            if item.text(0) == name:
                return item
            for i in range(item.childCount()):
                hit = find(item.child(i))
                if hit is not None:
                    return hit
            return None

        root = self.dialog.tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = find(root.child(i))
            if item is not None:
                self.dialog.tree.setCurrentItem(item)
                return
        raise AssertionError(f"no row {name!r} in {self.rows()}")


@pytest.fixture
def accounts(api: Client, tmp_path, qtbot, monkeypatch):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    api.register(EMAIL)
    book_id = UUID(api.make_book("Desk"))
    assets = UUID(api.account("Assets", "ASSET", placeholder=True)["id"])
    checking = UUID(api.account("Checking", "CHECKING")["id"])
    food = UUID(api.account("Food", "EXPENSE")["id"])
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "replica.sqlite")
    client.login(EMAIL, PASSWORD)
    yield client, book_id, assets, checking, food
    client.close()


def open_dialog(client, book_id, qtbot, *, can_edit: bool = True) -> AccountsDialog:
    book = client.books()[0]
    dialog = AccountsDialog(
        client,
        book_id,
        client.commodities(book_id),
        can_edit=can_edit,
        base_currency_id=book.base_currency_id,
        executor=SyncExecutor(),
    )
    qtbot.addWidget(dialog)
    return dialog


def fill(form: AccountForm, **fields: object) -> None:
    """Answer the form the way a person would, then press Save."""
    for key, value in fields.items():
        widget = getattr(form, key)
        if hasattr(widget, "setCurrentIndex") and isinstance(value, str):
            widget.setCurrentIndex(widget.findData(value))
        elif isinstance(value, bool):
            widget.setChecked(value)
        else:
            widget.setText(str(value))
    form._save()


# -- reading the chart ------------------------------------------------------
def test_the_tree_groups_by_class_then_by_hierarchy(accounts, qtbot):
    client, book_id, _assets, _checking, food = accounts
    client.create_account(
        book_id, AccountIn(name="Groceries", type="EXPENSE", parent_id=food)
    )
    dialog = open_dialog(client, book_id, qtbot)
    tree = Tree(dialog)

    assert tree.roots() == ["Assets", "Liabilities", "Income", "Expenses", "Equity"]
    rows = tree.rows()
    assert (1, "Assets (placeholder)", "Other asset", "EUR") in rows
    assert (1, "Checking", "Checking", "EUR") in rows
    # Food and its child: a parent always precedes its children.
    assert (1, "Food", "Expense", "EUR") in rows
    assert (2, "Groceries", "Expense", "EUR") in rows
    assert rows.index((1, "Food", "Expense", "EUR")) < rows.index(
        (2, "Groceries", "Expense", "EUR")
    )


def test_the_buttons_are_for_the_owner_alone(accounts, qtbot):
    client, book_id, _assets, checking, _food = accounts

    reader = open_dialog(client, book_id, qtbot, can_edit=False)
    assert not reader.add_button.isEnabled()
    assert not reader.edit_button.isEnabled()
    assert not reader.delete_button.isEnabled()
    assert reader.tree.topLevelItemCount() == 5
    assert "owners" in reader.blurb.text()

    owner = open_dialog(client, book_id, qtbot)
    assert owner.add_button.isEnabled()
    # A class root is not an account: nothing to edit until one is picked.
    assert not owner.edit_button.isEnabled()
    Tree(owner).select("Checking")
    assert owner.edit_button.isEnabled() and owner.delete_button.isEnabled()

    # An account class root, selected, still offers nothing to edit.
    owner.tree.setCurrentItem(owner.tree.topLevelItem(1))
    assert not owner.edit_button.isEnabled()
    assert checking is not None


# -- writing ----------------------------------------------------------------
def test_adding_an_account_under_the_selected_one(accounts, qtbot):
    client, book_id, assets, _checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    Tree(dialog).select("Assets")

    def run(form: AccountForm):
        fill(form, name="Savings", type="SAVINGS", parent_box=str(assets), code="200")
        return form.result()

    dialog.run_dialog = run
    dialog.add_account()

    saved = next(a for a in client.accounts(book_id) if a.name == "Savings")
    assert saved.path == "Assets:Savings"
    assert saved.code == "200"
    assert dialog.changed
    assert (2, "Savings", "Savings", "EUR") in Tree(dialog).rows()


def test_editing_sends_each_kind_of_change_to_its_own_endpoint(accounts, qtbot):
    client, book_id, assets, _checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    checking = next(a for a in client.accounts(book_id) if a.name == "Checking")
    Tree(dialog).select("Checking")

    def run(form: AccountForm):
        # Rename, move to the top level, change the metadata, and close: four
        # commands, each its own endpoint.
        fill(
            form,
            name="Chequing",
            parent_box="",
            institution="The Bank",
            iban="NL01",
            closed=True,
        )
        return form.result()

    dialog.run_dialog = run
    dialog.edit_account()

    edited = next(a for a in client.accounts(book_id) if a.id == checking.id)
    assert edited.name == "Chequing"
    assert edited.parent_id is None
    assert edited.institution == "The Bank" and edited.iban == "NL01"
    assert edited.closed is True
    assert (1, "Chequing (closed)", "Checking", "EUR") in Tree(dialog).rows()
    assert assets is not None


def test_editing_nothing_sends_nothing(accounts, qtbot):
    client, book_id, _assets, _checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    Tree(dialog).select("Food")

    def run(form: AccountForm):
        fill(form, name="Food")
        return form.result()

    dialog.run_dialog = run
    dialog.edit_account()
    assert not dialog.changed


def test_deleting_an_account(accounts, qtbot):
    client, book_id, _assets, checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    Tree(dialog).select("Checking")
    dialog.delete_account()

    assert all(a.id != checking for a in client.accounts(book_id))
    assert dialog.changed


def test_a_refusal_is_shown_and_the_tree_re_read(accounts, qtbot):
    client, book_id, _assets, checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    # Checking has a posting against it, so the server refuses the delete.
    food_id = next(a.id for a in client.accounts(book_id) if a.name == "Food")
    client.post_transaction(
        book_id,
        TransactionIn.model_validate(
            {
                "date": "2026-02-01",
                "legs": [
                    {"account_id": str(checking), "value": "-10.00"},
                    {"account_id": str(food_id)},
                ],
            }
        ),
    )
    dialog.reload()
    Tree(dialog).select("Checking")
    dialog.delete_account()
    assert "Could not delete" in dialog.status.text()
    assert any(a.id == checking for a in client.accounts(book_id))


# -- the form's own rules ---------------------------------------------------
def test_the_type_and_commodity_are_fixed_once_the_account_exists(accounts, qtbot):
    client, book_id, _assets, _checking, _food = accounts
    dialog = open_dialog(client, book_id, qtbot)
    _checking = next(a for a in client.accounts(book_id) if a.name == "Checking")

    def run(form: AccountForm):
        assert not form.type.isEnabled()
        assert not form.commodity.isEnabled()
        assert form.closed.isEnabled()  # closing is a field, so it is offered
        return form.result()

    dialog.run_dialog = run
    Tree(dialog).select("Checking")
    dialog.edit_account()


def test_a_sibling_with_the_same_name_is_refused_before_the_server_says_so(
    accounts, qtbot
):
    client, book_id, _assets, _checking, food = accounts
    other = client.create_account(
        book_id, AccountIn(name="Drinks", type="EXPENSE", parent_id=food)
    )

    form = AccountForm(
        client.accounts(book_id), client.commodities(book_id), existing=other
    )
    qtbot.addWidget(form)
    form.name.setText("food")  # the same name, only the case differs
    assert not form.save.isEnabled()
    assert "already exists here" in form.hint.text()

    form.name.setText("Drinks abroad")
    assert form.save.isEnabled()
    assert "already exists here" not in form.hint.text()


def test_an_account_cannot_become_its_own_parent(accounts, qtbot):
    client, book_id, _assets, _checking, food = accounts
    child = client.create_account(
        book_id, AccountIn(name="Drinks", type="EXPENSE", parent_id=food)
    )
    food_account = next(a for a in client.accounts(book_id) if a.id == food)
    form = AccountForm(
        client.accounts(book_id), client.commodities(book_id), existing=food_account
    )
    qtbot.addWidget(form)
    offered = [form.parent_box.itemData(i) for i in range(form.parent_box.count())]
    assert str(child.id) not in offered  # not one of its own descendants
    assert str(food) not in offered  # not itself


def test_merging_one_account_into_another(accounts, qtbot):
    client, book_id, _assets, checking, _food = accounts
    client.create_account(book_id, AccountIn(name="Old account", type="CHECKING"))
    dialog = open_dialog(client, book_id, qtbot)
    Tree(dialog).select("Old account")

    def run(form: AccountForm):
        assert form.merge_target.count() > 1
        form.merge_target.setCurrentIndex(1)
        form._ask_merge()
        assert "There is no undo" in form.merge_confirm.text()
        form._do_merge()
        return QDialog.DialogCode.Accepted

    dialog.run_dialog = run
    dialog.edit_account()

    names = {a.name for a in client.accounts(book_id)}
    assert "Old account" not in names
    assert "Checking" in names
    assert checking is not None


def test_a_lone_account_has_nothing_to_merge_into(accounts, qtbot, api: Client):
    client, book_id, _assets, _checking, _food = accounts
    api.login(EMAIL)
    dollars = api.post(
        f"/books/{book_id}/commodities",
        json={"code": "USD", "name": "US Dollar", "kind": "CURRENCY", "scale": 2},
    )
    assert dollars.status_code == 201, dollars.text
    lonely = client.create_account(
        book_id,
        AccountIn(name="Alone", type="CHECKING", commodity_id=dollars.json()["id"]),
    )
    dialog = open_dialog(client, book_id, qtbot)
    Tree(dialog).select("Alone")

    def run(form: AccountForm):
        # Nothing else is held in dollars, so there is nowhere to merge into.
        assert not hasattr(form, "merge_target")
        return QDialog.DialogCode.Rejected

    dialog.run_dialog = run
    dialog.edit_account()
    assert any(a.id == lonely.id for a in client.accounts(book_id))


# -- opened from the window -------------------------------------------------
@pytest.fixture
def window(accounts, qtbot):
    """The main window on the same book, driven synchronously."""
    from pymoney_desktop.ui.main_window import MainWindow

    client, book_id, assets, checking, food = accounts
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    qtbot.addWidget(win)
    win.load()
    yield win, client, book_id, assets, checking, food
    client.close()


def test_settings_offers_the_chart_of_accounts(window):
    win, *_ = window
    settings = next(
        a.menu() for a in win.menuBar().actions() if a.text().endswith("Settings")
    )
    assert settings is not None
    assert [a.text() for a in settings.actions()] == [
        "Accounts…",
        "Scheduled…",
        "Sync now",
    ]


def test_opening_it_from_the_window_changes_the_chart_of_the_main_tree(window):
    win, client, book_id, _assets, checking, _food = window

    def run(dialog):
        if isinstance(dialog, AccountsDialog):
            assert dialog.can_edit  # the window's user owns the book
            Tree(dialog).select("Checking")

            def answer(form: AccountForm):
                fill(form, name="Chequing")
                return form.result()

            dialog.run_dialog = answer
            dialog.edit_account()
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Accepted

    win.run_dialog = run  # type: ignore[method-assign]
    win.open_accounts()

    assert any(a.name == "Chequing" for a in client.accounts(book_id))
    # The window's own account tree followed the change.
    assert set(tree_texts(win)) >= {"Chequing", "Food"}
    assert checking is not None


def test_an_editor_gets_a_read_only_chart(window):
    win, *_ = window
    # Hand the book over, then come back as the editor.
    win.book = win.book.model_copy(update={"role": "editor"})

    def run(dialog):
        if isinstance(dialog, AccountsDialog):
            assert not dialog.can_edit
            assert not dialog.add_button.isEnabled()
        return QDialog.DialogCode.Accepted

    win.run_dialog = run  # type: ignore[method-assign]
    win.open_accounts()

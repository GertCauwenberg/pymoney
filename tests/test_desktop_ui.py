"""The PySide6 window, offscreen, against the real API through the offline client.

The window runs client calls through a synchronous executor here, so every
action completes before the next assertion; dialogs are answered by replacing
the window's `run_dialog`/`confirm` hooks rather than by clicking on them.
"""

from __future__ import annotations

import os
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog

from pymoney.api.schemas import (
    AccountOut,
    CommodityOut,
    PayeeOut,
)
from pymoney.desktop import OfflineClient
from pymoney.desktop.drafts import (
    LegDraft,
    TransactionDraft,
    parse_decimal,
    problems,
)
from pymoney.desktop.ui.main_window import (
    MainWindow,
    SyncExecutor,
    format_amount,
)
from pymoney.desktop.ui.transaction_dialog import (
    NEW_PAYEE,
    TransactionDialog,
)
from tests.conftest_api import PASSWORD, Client
from tests.test_desktop_clients import EMAIL, InProcessTransport


@pytest.fixture
def window(api: Client, tmp_path, qtbot, monkeypatch):
    # No modal boxes in a headless test: record them instead.
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
    visa = api.account("Visa", "CREDIT_CARD")
    food = api.account("Restaurants", "EXPENSE")
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
    yield win, api, UUID(book_id), UUID(visa["id"]), UUID(food["id"]), transport
    client.close()


def tree_texts(win: MainWindow) -> dict[str, str]:
    out = {}
    root = win.tree.invisibleRootItem()
    for i in range(root.childCount()):
        item = root.child(i)
        out[item.text(0)] = item.text(1)
    return out


def select_account(win: MainWindow, name: str) -> None:
    root = win.tree.invisibleRootItem()
    for i in range(root.childCount()):
        if root.child(i).text(0) == name:
            win.tree.setCurrentItem(root.child(i))
            return
    raise AssertionError(f"no account {name!r} in {tree_texts(win)}")


class TestMainWindow:
    def test_loads_the_book_and_its_accounts(self, window):
        win, *_ = window
        assert win.books.count() == 1 and win.books.currentText() == "Desk (owner)"
        assert set(tree_texts(win)) == {"Visa", "Restaurants"}
        assert (
            "local copy" in win.status_label.text()
            and "all sent" in win.status_label.text()
        )
        assert not win.new_button.isEnabled()
        select_account(win, "Visa")
        assert win.new_button.isEnabled()
        assert win.register.rowCount() == 0

    def test_post_edit_and_delete_through_the_dialog(self, window):
        win, api, book_id, visa, food, _transport = window
        select_account(win, "Visa")

        def fill(dialog: TransactionDialog) -> int:
            dialog.memo.setText("Dinner")
            dialog.payee.setCurrentIndex(dialog.payee.findData(NEW_PAYEE))
            dialog.new_payee.setText("Trattoria")
            dialog.legs.item(0, 3).setText("-62.50")
            combo = dialog.legs.cellWidget(1, 0)
            assert isinstance(combo, QComboBox)
            combo.setCurrentIndex(combo.findData(str(food)))
            assert dialog.footer.text() == "blank leg gets 62.50 EUR"
            dialog._accept()
            return dialog.result()

        win.run_dialog = fill  # type: ignore[method-assign]
        win.new_transaction()
        assert getattr(win, "reported", None) is None, win.reported
        assert win.boxes == [], win.boxes
        # The register shows it with the card's natural sign, and the tree's balance moved.
        assert win.register.rowCount() == 1
        row = [win.register.item(0, c).text() for c in range(6)]
        assert row[2:] == ["Trattoria", "Dinner", "62.50", "62.50"]
        assert tree_texts(win)["Visa"] == "62.50"
        assert tree_texts(win)["Restaurants"] == "62.50"
        assert (
            "2 waiting for the server" in win.status_label.text()
        )  # the payee and the transaction

        # Edit the memo: the dialog is prefilled with the stored figures.
        win.register.selectRow(0)
        win._enable_actions()
        assert win.edit_button.isEnabled()

        def edit(dialog: TransactionDialog) -> int:
            assert dialog.legs.item(0, 3).text() == "-62.50"
            assert dialog.legs.item(1, 3).text() == "62.50"
            assert dialog.footer.text() == "balanced"
            dialog.memo.setText("Dinner with Dave")
            dialog._accept()
            return dialog.result()

        win.run_dialog = edit  # type: ignore[method-assign]
        win.edit_transaction()
        assert win.register.item(0, 3).text() == "Dinner with Dave"

        # Sync: the server gets payee and transaction, with the same ids.
        win.sync()
        assert "all sent" in win.status_label.text()
        api.login(EMAIL)
        rows = api.get(f"/books/{book_id}/accounts/{visa}/register").json()["items"]
        assert [r["memo"] for r in rows] == ["Dinner with Dave"]
        assert [p["name"] for p in api.get(f"/books/{book_id}/payees").json()] == [
            "Trattoria"
        ]

        # Delete, after confirming.
        win.register.selectRow(0)
        win.confirm = lambda question: True  # type: ignore[method-assign]
        win.delete_transaction()
        assert win.register.rowCount() == 0
        win.sync()
        assert api.get(f"/books/{book_id}/accounts/{visa}/register").json()["items"] == []

    def test_the_dialog_refuses_an_incomplete_draft(self, window):
        win, *_ = window
        select_account(win, "Visa")
        captured = {}

        def incomplete(dialog: TransactionDialog) -> int:
            from PySide6.QtWidgets import QMessageBox

            warned = []
            QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2]))  # type: ignore[method-assign]
            dialog._accept()
            captured["warning"] = warned[0]
            return dialog.result()

        win.run_dialog = incomplete  # type: ignore[method-assign]
        win.new_transaction()
        assert "Leg 2: choose an account." in captured["warning"]
        assert win.register.rowCount() == 0

    def test_working_offline_then_reconnecting(self, window):
        win, _api, _book_id, _visa, food, transport = window
        select_account(win, "Visa")
        transport.plugged = False

        def fill(dialog: TransactionDialog) -> int:
            dialog.legs.item(0, 3).setText("-5")
            dialog.legs.cellWidget(1, 0).setCurrentIndex(
                dialog.legs.cellWidget(1, 0).findData(str(food))
            )
            dialog._accept()
            return dialog.result()

        win.run_dialog = fill  # type: ignore[method-assign]
        win.new_transaction()
        assert win.register.rowCount() == 1
        win.sync()
        assert win.status_label.text().startswith("Offline")
        assert "1 waiting" in win.status_label.text()
        transport.plugged = True
        win.sync()
        assert win.status_label.text().startswith("Online")
        assert "all sent" in win.status_label.text()

    def test_a_rejection_is_shown_and_the_copy_rebuilt(self, window):
        win, api, book_id, _visa, food, transport = window
        select_account(win, "Visa")
        transport.plugged = False

        def fill(dialog: TransactionDialog) -> int:
            dialog.legs.item(0, 3).setText("-5")
            dialog.legs.cellWidget(1, 0).setCurrentIndex(
                dialog.legs.cellWidget(1, 0).findData(str(food))
            )
            dialog._accept()
            return dialog.result()

        win.run_dialog = fill  # type: ignore[method-assign]
        win.new_transaction()
        api.login(EMAIL)
        assert api.delete(f"/books/{book_id}/accounts/{food}").status_code == 204
        transport.plugged = True
        win.sync()
        assert (
            not win.rejections_button.isHidden()
            and win.rejections_button.text() == "1 rejected…"
        )
        assert set(tree_texts(win)) == {"Visa"}
        assert win.register.rowCount() == 0


class TestDrafts:
    def test_parse_decimal(self):
        assert parse_decimal("12.5", 100) == 1250
        assert parse_decimal("-84,20", 100) == -8420
        assert parse_decimal("12.345", 100) is None
        assert parse_decimal("", 100) is None
        assert parse_decimal("abc", 100) is None
        assert parse_decimal("300", 1) == 300

    def test_problems(self):
        eur = CommodityOut(
            id=UUID(int=1),
            code="EUR",
            name="Euro",
            kind="CURRENCY",
            symbol=None,
            minor_unit_factor=100,
            display_precision=2,
            isin=None,
            quote_source=None,
            revision=1,
        )
        acc = AccountOut(
            id=UUID(int=2),
            name="Cash",
            path="Cash",
            type="CASH",
            account_class="ASSET",
            parent_id=None,
            commodity_id=eur.id,
            commodity="EUR",
            description="",
            code="",
            closed=False,
            placeholder=False,
            notes="",
            institution="",
            iban="",
            revision=1,
        )
        draft = TransactionDraft(
            date="2026-01-01", legs=[LegDraft(acc.id, ""), LegDraft(acc.id, "")]
        )
        assert problems(draft, {acc.id: acc}, eur) == [
            "Only one leg may be left blank to balance the entry."
        ]
        draft.legs[0].value = "1.999"
        assert problems(draft, {acc.id: acc}, eur) == ["Leg 1: not an amount in EUR."]

    def test_format_amount(self):
        from pymoney.api.schemas import AmountOut

        amount = AmountOut(
            commodity_id=UUID(int=1), commodity="EUR", minor=-123456, scale=100, text=""
        )
        assert format_amount(amount) == "-1,234.56"
        assert format_amount(amount, sign=-1) == "1,234.56"
        assert (
            format_amount(
                AmountOut(
                    commodity_id=UUID(int=1), commodity="JPY", minor=5, scale=1, text=""
                )
            )
            == "5"
        )


def test_dialog_builds_the_wire_body(qtbot):
    eur = CommodityOut(
        id=UUID(int=1),
        code="EUR",
        name="Euro",
        kind="CURRENCY",
        symbol=None,
        minor_unit_factor=100,
        display_precision=2,
        isin=None,
        quote_source=None,
        revision=1,
    )
    cash = AccountOut(
        id=UUID(int=2),
        name="Cash",
        path="Assets:Cash",
        type="CASH",
        account_class="ASSET",
        parent_id=None,
        commodity_id=eur.id,
        commodity="EUR",
        description="",
        code="",
        closed=False,
        placeholder=False,
        notes="",
        institution="",
        iban="",
        revision=1,
    )
    food = AccountOut(
        id=UUID(int=3),
        name="Food",
        path="Expenses:Food",
        type="EXPENSE",
        account_class="EXPENSE",
        parent_id=None,
        commodity_id=eur.id,
        commodity="EUR",
        description="",
        code="",
        closed=False,
        placeholder=False,
        notes="",
        institution="",
        iban="",
        revision=1,
    )
    payee = PayeeOut(
        id=UUID(int=4),
        name="Shop",
        notes="",
        address="",
        match_keys=[],
        default_account_id=None,
        revision=1,
    )
    dialog = TransactionDialog([cash, food], [payee], eur, default_account_id=cash.id)
    qtbot.addWidget(dialog)
    dialog.legs.item(0, 3).setText("-10")
    dialog.legs.cellWidget(1, 0).setCurrentIndex(
        dialog.legs.cellWidget(1, 0).findData(str(food.id))
    )
    dialog.payee.setCurrentIndex(dialog.payee.findData(str(payee.id)))
    body = dialog.draft().to_wire({cash.id: cash, food.id: food}, eur)
    assert body.payee_id == payee.id
    assert [(leg.account_id, leg.value) for leg in body.legs] == [
        (cash.id, "-10"),
        (food.id, None),
    ]
    assert (
        body.model_dump(mode="json")["legs"][0]["value"] == "-10"
    )  # a string, never a float
    assert dialog.windowTitle() == "New transaction"
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert Qt.AlignmentFlag.AlignRight  # Qt imported for real


def test_the_threaded_executor_calls_back_on_the_ui_thread(qtbot):
    import threading

    from pymoney.desktop.ui.main_window import ThreadedExecutor

    executor = ThreadedExecutor()
    seen: dict[str, object] = {}

    def work():
        seen["worker"] = threading.get_ident()
        return 42

    executor.run(
        work,
        lambda value: seen.__setitem__("done", (value, threading.get_ident())),
        lambda exc: None,
    )
    qtbot.waitUntil(lambda: "done" in seen, timeout=5000)
    assert seen["done"][0] == 42
    assert seen["worker"] != threading.get_ident()
    assert seen["done"][1] == threading.get_ident()

    executor.run(
        lambda: 1 / 0, lambda value: None, lambda exc: seen.__setitem__("failed", exc)
    )
    qtbot.waitUntil(lambda: "failed" in seen, timeout=5000)
    assert isinstance(seen["failed"], ZeroDivisionError)


def test_login_dialog_fields(qtbot):
    from pymoney.desktop.ui.login_dialog import LoginDialog, default_replica_path

    dialog = LoginDialog(url="http://x", email="a@b", offline=False)
    qtbot.addWidget(dialog)
    assert dialog.url.text() == "http://x" and dialog.email.text() == "a@b"
    assert not dialog.offline.isChecked()
    assert default_replica_path().name == "replica.sqlite"


def test_the_desktop_speaks_dutch_when_told(qtbot, api: Client, tmp_path):
    from pymoney.desktop.i18n import set_locale
    from pymoney.desktop.ui.login_dialog import LoginDialog

    api.register(EMAIL)
    api.make_book("Taal")
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "nl.sqlite")
    client.login(EMAIL, PASSWORD)
    try:
        set_locale("nl")
        dialog = LoginDialog()
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "PyMoney — aanmelden"
        assert dialog.offline.text() == "Een lokale kopie bewaren en offline werken"
        assert dialog.chosen_locale() == "nl"
        # Switching the combo relabels the dialog at once.
        dialog.language.setCurrentIndex(dialog.language.findData("en"))
        assert dialog.windowTitle() == "PyMoney — log in"
        set_locale("nl")
        win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
        qtbot.addWidget(win)
        assert win.new_button.text() == "Nieuwe transactie"
        assert [a.text() for a in win.report_actions] == [
            "&Balans…",
            "&Resultatenrekening…",
            "&Posities…",
        ]
        win.load()
        assert (
            "lokale kopie" in win.status_label.text()
            and "alles verstuurd" in win.status_label.text()
        )
        # The choice is stored with the account for every other client.
        assert client.set_locale("nl").locale == "nl"
        api.login(EMAIL)
        assert api.get("/auth/me").json()["locale"] == "nl"
    finally:
        set_locale("en")
        client.close()

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

from pymoney.api.schemas import (
    AccountOut,
    CommodityOut,
    PayeeOut,
    TagOut,
    TransactionOut,
)
from pymoney.api.testing import PASSWORD, Client
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog

from pymoney_desktop import OfflineClient
from pymoney_desktop.drafts import (
    LegDraft,
    TransactionDraft,
    parse_decimal,
    problems,
)
from pymoney_desktop.ui.main_window import (
    MainWindow,
    SyncExecutor,
    format_amount,
)
from pymoney_desktop.ui.transaction_dialog import (
    NEW_PAYEE,
    TransactionDialog,
)
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

    def test_attachments_dialog_adds_downloads_and_removes(
        self, window, monkeypatch, tmp_path
    ):
        from PySide6.QtWidgets import QFileDialog

        from pymoney_desktop.ui.attachments_dialog import AttachmentsDialog

        win, api, book_id, visa, food, _transport = window
        select_account(win, "Visa")

        def fill(dialog: TransactionDialog) -> int:
            dialog.legs.item(0, 3).setText("-12.00")
            combo = dialog.legs.cellWidget(1, 0)
            combo.setCurrentIndex(combo.findData(str(food)))
            dialog._accept()
            return dialog.result()

        win.run_dialog = fill  # type: ignore[method-assign]
        win.new_transaction()
        win.sync()  # attachments need a transaction the *server* knows
        win.register.selectRow(0)
        win._enable_actions()
        assert win.attachments_button.isEnabled()

        png = tmp_path / "receipt.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        saved = tmp_path / "copy.png"
        monkeypatch.setattr(
            QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(png), ""))
        )
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(saved), ""))
        )

        dialogs: list[AttachmentsDialog] = []
        win.run_dialog = lambda d: (dialogs.append(d), d.reject())[1]  # type: ignore[method-assign,assignment]
        win.open_attachments()
        dialog = dialogs[0]
        assert dialog.list.count() == 0

        dialog._add()
        assert dialog.list.count() == 1
        assert dialog.attachments[0].filename == "receipt.png"

        dialog.list.setCurrentRow(0)
        dialog._save()
        assert saved.read_bytes() == png.read_bytes()

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
        )
        dialog.list.setCurrentRow(0)
        dialog._remove()
        assert dialog.list.count() == 0

        # And the server really has nothing left.
        api.login(EMAIL)
        row = api.get(f"/books/{book_id}/accounts/{visa}/register").json()["items"][0]
        assert (
            api.get(
                f"/books/{book_id}/transactions/{row['transaction_id']}/attachments"
            ).json()
            == []
        )

    def test_recording_a_stock_split_from_the_register(self, window):
        from pymoney_desktop.ui.stock_split_dialog import StockSplitDialog

        win, api, book_id, _visa, _food, _transport = window
        api.login(EMAIL)
        acme = api.post(
            f"/books/{book_id}/commodities",
            json={"code": "ACME", "kind": "STOCK", "scale": 4},
        ).json()
        shares = api.account("ACME", "STOCK", commodity_id=acme["id"])
        cash = api.account("Brokerage Cash", "CASH")
        api.post_transaction(
            "2026-01-05",
            [
                {"account_id": shares["id"], "quantity": "10", "price": "50.00"},
                {"account_id": cash["id"], "value": "-500.00"},
            ],
        )
        api.logout()
        win.sync()  # the replica learns about the new security and account
        win._reload_book()

        # The split button is offered on a holding, not on a plain EUR account.
        select_account(win, "Visa")
        assert not win.split_button.isEnabled()
        select_account(win, "ACME")
        assert win.split_button.isEnabled()

        from PySide6.QtCore import QDate

        def fill(dialog: StockSplitDialog) -> int:
            assert isinstance(dialog, StockSplitDialog)
            dialog.date.setDate(QDate(2026, 3, 1))
            dialog.ratio.setText("3/2")
            dialog.memo.setText("3-for-2")
            dialog._accept()
            return dialog.result()

        win.run_dialog = fill  # type: ignore[method-assign]
        win.record_stock_split()
        win.sync()

        api.login(EMAIL)
        register = api.get(f"/books/{book_id}/accounts/{shares['id']}/register").json()[
            "items"
        ]
        split_row = next(
            r
            for r in register
            if api.get(f"/books/{book_id}/transactions/{r['transaction_id']}").json()[
                "is_stock_split"
            ]
        )
        assert split_row["quantity"]["minor"] == 50000  # +5 shares: 10 -> 15

        # The split shows on the register; the transaction window refuses to
        # edit it and explains why.
        win._reload_book()
        win.register.selectRow(
            next(
                i
                for i, r in enumerate(win.rows)
                if str(r.transaction_id) == split_row["transaction_id"]
            )
        )
        win._enable_actions()
        win.edit_transaction()
        assert any("not edited" in text for _title, text in win.boxes)

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
        row = [win.register.item(0, c).text() for c in range(7)]
        # payee, memo, the (empty) reconciliation mark, amount, balance
        assert row[2:] == ["Trattoria", "Dinner", "", "62.50", "62.50"]
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


def _commodity(n: int, code: str) -> CommodityOut:
    return CommodityOut(
        id=UUID(int=n),
        code=code,
        name=code,
        kind="CURRENCY",
        symbol=None,
        minor_unit_factor=100,
        display_precision=2,
        isin=None,
        quote_source=None,
        revision=1,
    )


def _amount(commodity_id: UUID, code: str, minor: int) -> dict:
    return {
        "commodity_id": str(commodity_id),
        "commodity": code,
        "minor": minor,
        "scale": 100,
        "text": f"{minor / 100:.2f}",
    }


def _account(n: int, path: str, commodity: CommodityOut) -> AccountOut:
    return AccountOut(
        id=UUID(int=n),
        name=path.split(":")[-1],
        path=path,
        type="CASH",
        account_class="ASSET",
        parent_id=None,
        commodity_id=commodity.id,
        commodity=commodity.code,
        description="",
        code="",
        closed=False,
        placeholder=False,
        notes="",
        institution="",
        iban="",
        revision=1,
    )


def test_dialog_lets_a_new_transaction_choose_its_currency(qtbot):
    eur, usd = _commodity(1, "EUR"), _commodity(2, "USD")
    cash = _account(10, "Assets:Cash", eur)
    wallet = _account(11, "Assets:Dollar Wallet", usd)

    dialog = TransactionDialog(
        [cash, wallet], [], eur, commodities=[eur, usd], default_account_id=cash.id
    )
    qtbot.addWidget(dialog)
    assert dialog.currency_box.isEnabled()
    assert dialog.currency().code == "EUR"

    dialog.currency_box.setCurrentIndex(dialog.currency_box.findData(str(usd.id)))
    assert dialog.currency().code == "USD"
    assert dialog.legs.horizontalHeaderItem(3).text() == "Amount (USD)"

    # A leg in another commodity no longer demands a quantity: the server
    # values it from the book's prices, or mints one if a quantity is given.
    dialog.currency_box.setCurrentIndex(dialog.currency_box.findData(str(eur.id)))
    dialog.legs.item(0, 3).setText("-90")
    dialog.legs.cellWidget(1, 0).setCurrentIndex(
        dialog.legs.cellWidget(1, 0).findData(str(wallet.id))
    )
    dialog.legs.item(1, 3).setText("90")
    assert problems(dialog.draft(), {cash.id: cash, wallet.id: wallet}, eur) == []
    body = dialog.draft().to_wire({cash.id: cash, wallet.id: wallet}, eur)
    assert body.legs[1].quantity is None

    dialog.legs.item(1, 2).setText("100")
    body = dialog.draft().to_wire({cash.id: cash, wallet.id: wallet}, eur)
    assert body.legs[1].quantity == "100"


def test_dialog_fixes_the_currency_while_editing(qtbot):
    eur, usd = _commodity(1, "EUR"), _commodity(2, "USD")
    cash = _account(10, "Assets:Cash", eur)
    food = _account(11, "Expenses:Food", eur)
    existing = TransactionOut(
        id=UUID(int=20),
        date="2026-02-01",
        currency_id=usd.id,
        currency="USD",
        memo="Lunch abroad",
        payee_id=None,
        number="",
        rounding="HALF_EVEN",
        entered_at=None,
        imported_id=None,
        reconcile_state="NOT_RECONCILED",
        locked=False,
        is_stock_split=False,
        revision=1,
        splits=[
            {
                "id": str(UUID(int=21)),
                "account_id": str(cash.id),
                "quantity": _amount(usd.id, "USD", -1000),
                "value": _amount(usd.id, "USD", -1000),
                "memo": "",
                "payee_id": None,
                "reconcile_state": "NOT_RECONCILED",
                "reconcile_date": None,
                "action": "",
                "number": "",
                "price": None,
                "implied_price": None,
            },
            {
                "id": str(UUID(int=22)),
                "account_id": str(food.id),
                "quantity": _amount(usd.id, "USD", 1000),
                "value": _amount(usd.id, "USD", 1000),
                "memo": "",
                "payee_id": None,
                "reconcile_state": "NOT_RECONCILED",
                "reconcile_date": None,
                "action": "",
                "number": "",
                "price": None,
                "implied_price": None,
            },
        ],
    )
    dialog = TransactionDialog(
        [cash, food], [], usd, commodities=[eur, usd], existing=existing
    )
    qtbot.addWidget(dialog)
    assert not dialog.currency_box.isEnabled()
    assert dialog.currency().code == "USD"
    assert dialog.legs.horizontalHeaderItem(3).text() == "Amount (USD)"


def _tag(n: int, name: str) -> TagOut:
    return TagOut(
        id=UUID(int=n), name=name, notes="", colour=None, closed=False, revision=1
    )


def test_dialog_separates_known_tags_from_typed_new_ones(qtbot):
    from pymoney_desktop.ui.transaction_dialog import TAGS

    eur = _commodity(1, "EUR")
    cash = _account(10, "Assets:Cash", eur)
    food = _account(11, "Expenses:Food", eur)
    holiday = _tag(30, "Holiday")
    dialog = TransactionDialog(
        [cash, food], [], eur, tags=[holiday], default_account_id=cash.id
    )
    qtbot.addWidget(dialog)
    dialog.legs.item(0, 3).setText("-40")
    dialog.legs.cellWidget(1, 0).setCurrentIndex(
        dialog.legs.cellWidget(1, 0).findData(str(food.id))
    )
    dialog.legs.cellWidget(1, TAGS).setText(" holiday , Spain; Spain ")
    second = dialog.draft().legs[1]
    assert second.tag_ids == [holiday.id]  # matched, case-insensitively
    assert second.new_tag_names == ["Spain"]  # typed, not in the book, deduped

    wire = second.to_wire({cash.id: cash, food.id: food}, eur.id)
    assert wire.tag_ids == [holiday.id]  # only resolved ids travel


def test_dialog_shows_the_tags_a_stored_leg_already_wears(qtbot):
    from pymoney_desktop.ui.transaction_dialog import TAGS

    eur = _commodity(1, "EUR")
    cash = _account(10, "Assets:Cash", eur)
    food = _account(11, "Expenses:Food", eur)
    holiday, work = _tag(30, "Holiday"), _tag(31, "Work")
    existing = TransactionOut(
        id=UUID(int=40),
        date="2026-05-18",
        currency_id=eur.id,
        currency="EUR",
        memo="",
        payee_id=None,
        number="",
        is_stock_split=False,
        rounding="HALF_EVEN",
        entered_at=None,
        imported_id=None,
        reconcile_state="NOT_RECONCILED",
        locked=False,
        revision=1,
        splits=[
            {
                "id": str(UUID(int=41)),
                "account_id": str(cash.id),
                "quantity": _amount(eur.id, "EUR", -4000),
                "value": _amount(eur.id, "EUR", -4000),
                "memo": "",
                "payee_id": None,
                "reconcile_state": "NOT_RECONCILED",
                "reconcile_date": None,
                "action": "",
                "number": "",
                "price": None,
                "implied_price": None,
                "tag_ids": [],
            },
            {
                "id": str(UUID(int=42)),
                "account_id": str(food.id),
                "quantity": _amount(eur.id, "EUR", 4000),
                "value": _amount(eur.id, "EUR", 4000),
                "memo": "",
                "payee_id": None,
                "reconcile_state": "NOT_RECONCILED",
                "reconcile_date": None,
                "action": "",
                "number": "",
                "price": None,
                "implied_price": None,
                "tag_ids": [str(holiday.id), str(work.id)],
            },
        ],
    )
    dialog = TransactionDialog(
        [cash, food], [], eur, tags=[holiday, work], existing=existing
    )
    qtbot.addWidget(dialog)
    assert dialog.legs.cellWidget(1, TAGS).text() == "Holiday, Work"
    assert dialog.draft().legs[1].tag_ids == [holiday.id, work.id]


def test_the_threaded_executor_calls_back_on_the_ui_thread(qtbot):
    import threading

    from pymoney_desktop.ui.main_window import ThreadedExecutor

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
    from pymoney_desktop.ui.login_dialog import LoginDialog, default_replica_path

    dialog = LoginDialog(url="http://x", email="a@b", offline=False)
    qtbot.addWidget(dialog)
    assert dialog.url.text() == "http://x" and dialog.email.text() == "a@b"
    assert not dialog.offline.isChecked()
    assert default_replica_path().name == "replica.sqlite"


def test_the_desktop_speaks_dutch_when_told(qtbot, api: Client, tmp_path):
    from pymoney_desktop.i18n import set_locale
    from pymoney_desktop.ui.login_dialog import LoginDialog

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
            "&Inkomsten per categorie…",
            "&Uitgaven per categorie…",
            "&Vermogen in de tijd…",
            "&Inkomsten en uitgaven per periode…",
            "&Beleggingen per positie…",
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


class TestSearch:
    def test_the_search_window_finds_and_opens_a_transaction(self, window):
        from datetime import date

        from pymoney.api.schemas import TransactionIn

        win, _api, book_id, visa, food, _transport = window
        # Two entries on the card, through the client, so the replica has them.
        for amount, memo, when in (
            ("-62.50", "Dinner", "2026-03-14"),
            ("-8.00", "Coffee", "2026-03-20"),
        ):
            win.client.post_transaction(
                book_id,
                TransactionIn.model_validate(
                    {
                        "date": when,
                        "legs": [
                            {"account_id": str(visa), "value": amount},
                            {"account_id": str(food), "value": None},
                        ],
                        "memo": memo,
                    }
                ),
            )
        win.load()
        search = win.open_search()
        assert search is not None and win.search_button.isEnabled()
        # Opening it again brings the same window forward rather than a second one.
        assert win.open_search() is search

        # Nothing filled in: refused locally, no request made.
        search.search()
        assert "at least one" in search.status.text()

        search.text.setText("din")
        search.search()
        assert [t.memo for t in search.results] == ["Dinner"]
        assert search.table.rowCount() == 1
        assert search.table.item(0, 3).text() == "Visa → Restaurants"
        assert search.table.item(0, 4).text() == "62.50 EUR"
        assert search.status.text() == "One transaction matches."

        # An amount range and a date, combined.
        search.clear()
        assert search.table.rowCount() == 0
        search.amount_min.setText("5")
        search.amount_max.setText("100")
        search.use_start.setChecked(True)
        search.start.setDate(_qdate(date(2026, 3, 15)))
        search.search()
        assert [t.memo for t in search.results] == ["Coffee"]

        # A nonsense amount is the server's refusal, shown, not a crash.
        search.amount_min.setText("abc")
        search.search()
        assert search.last_error is not None and search.results == []

        # Opening a result: the main window shows the card's register with the
        # row selected and, since the owner may edit, opens the transaction.
        opened: list = []
        win.run_dialog = lambda dialog: opened.append(dialog.windowTitle()) or 0  # type: ignore[method-assign]
        search.clear()
        search.text.setText("coffee")
        search.search()
        search.table.selectRow(0)
        search.open_selected()
        assert win.current_account is not None and win.current_account.name == "Visa"
        selected = win._selected_row()
        assert selected is not None and selected.memo == "Coffee"
        assert opened == ["Edit transaction"]


def _qdate(value):
    from PySide6.QtCore import QDate

    return QDate(value.year, value.month, value.day)


class TestReconciliation:
    def _post(self, win, book_id, visa, food, amount, memo, when):
        from pymoney.api.schemas import TransactionIn

        return win.client.post_transaction(
            book_id,
            TransactionIn.model_validate(
                {
                    "date": when,
                    "legs": [
                        {"account_id": str(visa), "value": amount},
                        {"account_id": str(food), "value": None},
                    ],
                    "memo": memo,
                }
            ),
        )

    def test_mark_one_row_and_reconcile_a_statement(self, window):
        from PySide6.QtCore import Qt

        win, _api, book_id, visa, food, _transport = window
        self._post(win, book_id, visa, food, "-62.50", "Dinner", "2026-03-14")
        self._post(win, book_id, visa, food, "-8.00", "Coffee", "2026-03-20")
        win.load()
        select_account(win, "Visa")
        assert win.register.columnCount() == 7
        assert [win.register.item(i, 4).text() for i in range(2)] == ["", ""]
        assert win.reconcile_button.isEnabled()

        # Mark the selected row cleared through the menu.
        win.register.selectRow(0)
        assert win.mark_button.isEnabled()
        assert win.mark_actions["FROZEN"].isVisible()  # the owner may freeze
        win.mark_actions["CLEARED"].trigger()
        assert win.register.item(0, 4).text() == "C"
        assert win.register.item(0, 4).toolTip() == "Cleared"

        # The statement dialog: the cleared row is pre-ticked; tick the other,
        # mark both reconciled.
        captured = {}

        def run(dialog):
            captured["dialog"] = dialog
            assert dialog.state_box.count() == 3  # owner: Frozen offered
            ticks = [
                dialog.table.item(i, 0).checkState() == Qt.CheckState.Checked
                for i in range(2)
            ]
            assert ticks == [True, False]
            assert dialog.summary.text().startswith("1 selected")
            dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
            assert dialog.summary.text().startswith("2 selected")
            assert "70.50" in dialog.summary.text()
            dialog.state_box.setCurrentIndex(1)  # Reconciled
            dialog.date.setDate(_qdate(date(2026, 3, 31)))
            return 1  # Accepted

        win.run_dialog = run  # type: ignore[method-assign]
        win.reconcile_account()
        assert "dialog" in captured
        assert [win.register.item(i, 4).text() for i in range(2)] == ["R", "R"]
        # Reconciled rows are locked: editing is refused with a message.
        win.register.selectRow(0)
        win.edit_transaction()
        assert win.boxes and win.boxes[-1][0] == "Locked"

        # Freeze one as the owner; frozen rows offer no Mark at all.
        win.mark_actions["FROZEN"].trigger()
        assert win.register.item(0, 4).text() == "F"
        win.register.selectRow(0)
        win._enable_actions()
        assert not win.mark_button.isEnabled()


from datetime import date  # noqa: E402


class TestQuickEntryAndStatementCheck:
    def test_the_payees_default_account_fills_the_blank_leg(self, window):
        from pymoney.api.schemas import PayeeIn

        from pymoney_desktop.ui.transaction_dialog import TransactionDialog

        win, _api, book_id, _visa, food, _transport = window
        win.client.create_payee(
            book_id, PayeeIn(name="Trattoria", default_account_id=food)
        )
        win.load()
        currency = win._currency()
        assert currency is not None
        dialog = TransactionDialog(
            win.accounts, win.payees, currency, default_account_id=None
        )
        dialog.payee.setCurrentIndex(dialog.payee.findText("Trattoria"))
        second = dialog.legs.cellWidget(1, 0)
        assert second.currentData() == str(food)
        # Only a blank leg is filled: choosing again does not overwrite a choice.
        visa_index = (
            second.findText(win.accounts[0].path)
            if second.findText(win.accounts[0].path) >= 0
            else 1
        )
        second.setCurrentIndex(visa_index)
        chosen = second.currentData()
        dialog.payee.setCurrentIndex(0)
        dialog.payee.setCurrentIndex(dialog.payee.findText("Trattoria"))
        assert second.currentData() == chosen

    def test_the_statement_balance_check(self, window):
        from pymoney_desktop.ui.reconcile import ReconcileDialog

        win, _api, book_id, visa, food, _transport = window
        TestReconciliation._post(
            None, win, book_id, visa, food, "-62.50", "Dinner", "2026-03-14"
        )
        TestReconciliation._post(
            None, win, book_id, visa, food, "-8.00", "Coffee", "2026-03-20"
        )
        win.load()
        select_account(win, "Visa")
        dialog = ReconcileDialog(
            win.current_account, win.rows, {}, opening_minor=win.register_opening_minor
        )
        assert dialog.difference() is None
        # Tick the dinner only; the card statement says 62.50 owed: a match.
        from PySide6.QtCore import Qt

        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.stated.setText("62.50")
        assert dialog.difference() == 0
        assert "matches the statement" in dialog.summary.text()
        # The statement says 70.50: the coffee is missing, and the summary says by how much.
        dialog.stated.setText("70.50")
        assert (
            dialog.difference() == -800 * 1
        )  # raw sign: the card stores -70.50, we have -62.50
        assert "differs from the statement by 8.00" in dialog.summary.text()
        dialog.stated.setText("abc")
        assert dialog.difference() is None

"""The main window: books, the account tree with balances, a register, and
the status bar that tells you whether your work has reached the server.

Every call to the client runs off the UI thread through :class:`Executor`; a
test passes a synchronous one so the window can be driven step by step.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    AmountOut,
    BookOut,
    CommodityOut,
    PayeeIn,
    PayeeOut,
    ReconcileStateName,
    RegisterRowOut,
    TransactionOut,
)
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import ApiError, LedgerClient, OfflineError
from ..i18n import _, current_locale
from .theme import ACCENT, amount_brush
from .transaction_dialog import TransactionDialog

__all__ = ["Executor", "MainWindow", "SyncExecutor", "ThreadedExecutor"]

CREDIT_NORMAL = {"LIABILITY", "EQUITY", "INCOME"}

# The reconciliation column, as the web app shows it (ReconcileMark.tsx).
RECONCILE_MARKS = {"NOT_RECONCILED": "", "CLEARED": "C", "RECONCILED": "R", "FROZEN": "F"}
RECONCILE_LABELS = {
    "NOT_RECONCILED": "Not reconciled",
    "CLEARED": "Cleared",
    "RECONCILED": "Reconciled",
    "FROZEN": "Frozen",
}


# Thousands and decimal separators per language; the digits never go through a float.
SEPARATORS = {"en": (",", "."), "nl": (".", ",")}


def format_amount(amount: AmountOut, *, sign: int = 1) -> str:
    digits = len(str(amount.scale)) - 1
    minor = amount.minor * sign
    negative = minor < 0
    s = str(abs(minor)).rjust(digits + 1, "0")
    whole, frac = (s[:-digits], s[-digits:]) if digits else (s, "")
    group, point = SEPARATORS.get(current_locale(), SEPARATORS["en"])
    whole = f"{int(whole):,}".replace(",", group)
    text = f"{whole}{point}{frac}" if frac else whole
    return f"-{text}" if negative else text


def natural_sign(account_class: str) -> int:
    return -1 if account_class in CREDIT_NORMAL else 1


# -- running client calls off the UI thread -------------------------------------
class Executor:
    def run(
        self,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[Exception], None],
    ) -> None:
        raise NotImplementedError


class SyncExecutor(Executor):
    """For tests: run it now, call back now."""

    def run(self, work, done, failed) -> None:
        try:
            result = work()
        except Exception as exc:
            failed(exc)
            return
        done(result)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, work: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self.work, self.signals = work, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.work())
        except Exception as exc:
            self.signals.failed.emit(exc)


class ThreadedExecutor(Executor):
    """One worker thread: the clients are not thread-safe, and one call at a time is plenty."""

    def __init__(self) -> None:
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self._live: list[_Signals] = []

    def run(self, work, done, failed) -> None:
        signals = _Signals()
        self._live.append(signals)

        def finish(callback, value):
            self._live.remove(signals)
            callback(value)

        signals.done.connect(lambda value: finish(done, value))
        signals.failed.connect(lambda exc: finish(failed, exc))
        self.pool.start(_Job(work, signals))


# -- the window --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(
        self,
        client: LedgerClient,
        *,
        executor: Executor | None = None,
        sync_every_ms: int = 60_000,
    ) -> None:
        super().__init__()
        self.client = client
        self.executor = executor or ThreadedExecutor()
        self.setWindowTitle(_("PyMoney"))
        self.resize(1100, 700)
        self.book: BookOut | None = None
        self.accounts: list[AccountOut] = []
        self.commodities: list[CommodityOut] = []
        self.payees: list[PayeeOut] = []
        self.rows: list[RegisterRowOut] = []
        self.register_opening_minor = 0
        self.current_account: AccountOut | None = None

        bar = QToolBar("Main")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.books = QComboBox()
        self.books.setMinimumWidth(220)
        self.books.currentIndexChanged.connect(self._book_chosen)
        bar.addWidget(QLabel(" Book: "))
        bar.addWidget(self.books)
        bar.addSeparator()
        self.new_button = QPushButton(_("New transaction"))
        self.new_button.clicked.connect(self.new_transaction)
        bar.addWidget(self.new_button)
        self.edit_button = QPushButton(_("Edit"))
        self.edit_button.clicked.connect(self.edit_transaction)
        bar.addWidget(self.edit_button)
        # Reconciliation: one row's mark, or a whole statement at once.
        self.mark_button = QToolButton()
        self.mark_button.setText(_("Mark"))
        self.mark_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mark_menu = QMenu(self.mark_button)
        self.mark_actions: dict[str, QAction] = {}
        for state in ("NOT_RECONCILED", "CLEARED", "RECONCILED", "FROZEN"):
            action = QAction(_(RECONCILE_LABELS[state]), self)
            action.setData(state)
            action.triggered.connect(lambda _=False, s=state: self.mark_selected(s))
            self.mark_menu.addAction(action)
            self.mark_actions[state] = action
        self.mark_button.setMenu(self.mark_menu)
        bar.addWidget(self.mark_button)
        self.reconcile_button = QPushButton(_("Reconcile…"))
        self.reconcile_button.setToolTip(
            _("Tick the entries on a statement and mark them all at once")
        )
        self.reconcile_button.clicked.connect(self.reconcile_account)
        bar.addWidget(self.reconcile_button)
        self.search_button = QPushButton(_("Search…"))
        self.search_button.setShortcut(QKeySequence.StandardKey.Find)
        self.search_button.setToolTip(
            _("Find transactions by text, amount, date, account or payee (Ctrl+F)")
        )
        self.search_button.clicked.connect(self.open_search)
        bar.addWidget(self.search_button)
        self.search_window: QMainWindow | None = None
        self._select_after_load: UUID | None = None
        self.delete_button = QPushButton(_("Delete"))
        self.delete_button.clicked.connect(self.delete_transaction)
        bar.addWidget(self.delete_button)
        bar.addSeparator()
        self.sync_button = QPushButton(_("Sync now"))
        self.sync_button.clicked.connect(self.sync)
        bar.addWidget(self.sync_button)
        self.logout_button = QPushButton(_("Log out"))
        bar.addWidget(self.logout_button)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Account"), _("Balance")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(
            lambda current, _: self._account_chosen(current)
        )

        self.register = QTableWidget(0, 7)
        self.register.setHorizontalHeaderLabels(
            [_("Date"), _("No."), _("Payee"), _("Memo"), "R", _("Amount"), _("Balance")]
        )
        self.register.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.register.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.register.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.register.doubleClicked.connect(lambda _index: self.edit_transaction())
        # Edit, Delete and Mark follow the selection; without this they only
        # caught up on the next reload.
        self.register.itemSelectionChanged.connect(self._enable_actions)
        self.register_title = QLabel(_("Choose an account"))
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.register_title)
        right_layout.addWidget(self.register)

        splitter = QSplitter()
        splitter.addWidget(self.tree)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        # -- menus -------------------------------------------------------------
        reports = self.menuBar().addMenu("&Reports")
        self.report_actions: list[QAction] = []
        for kind, label in (
            ("balance-sheet", "Balance sheet"),
            ("income-statement", "Income statement"),
            ("holdings", "Holdings"),
        ):
            action = QAction(f"&{_(label)}…", self)
            action.setData(kind)
            action.triggered.connect(lambda _=False, k=kind: self.open_report(k))
            reports.addAction(action)
            self.report_actions.append(action)
        reports.addSeparator()
        for kind, label in (
            ("income-pie", "Income by category"),
            ("expense-pie", "Expenses by category"),
            ("net-worth", "Net worth over time"),
            ("period-table", "Income and expenses by period"),
            ("holdings-pie", "Holdings by position"),
        ):
            action = QAction(f"&{_(label)}…", self)
            action.setData(kind)
            action.triggered.connect(lambda _=False, k=kind: self.open_chart(k))
            reports.addAction(action)
            self.report_actions.append(action)
        self.report_windows: list[QMainWindow] = []

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.rejections_button = QPushButton(_("Rejected…"))
        self.rejections_button.clicked.connect(self.show_rejections)
        self.rejections_button.setVisible(False)
        self.statusBar().addPermanentWidget(self.rejections_button)

        self._enable_actions()
        self._refresh_status()
        if sync_every_ms > 0:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.sync)
            self._timer.start(sync_every_ms)

    # -- loading -------------------------------------------------------------------------
    def load(self) -> None:
        self.executor.run(
            self.client.books,
            self._books_loaded,
            self._failed(_("Could not list the books")),
        )

    def _books_loaded(self, books: list[BookOut]) -> None:
        self.books.blockSignals(True)
        self.books.clear()
        for b in books:
            self.books.addItem(f"{b.name} ({_(str(b.role))})", b)
        self.books.blockSignals(False)
        if books:
            self.books.setCurrentIndex(0)
            self._book_chosen(0)
        self._refresh_status()

    def _book_chosen(self, index: int) -> None:
        book = self.books.itemData(index) if index >= 0 else None
        if not isinstance(book, BookOut):
            return
        if self.book is None or self.book.id != book.id:
            self.current_account = None  # a different book: nothing is selected yet
        self.book = book
        self._enable_actions()
        book_id = book.id

        def work():
            return (
                self.client.accounts(book_id),
                self.client.commodities(book_id),
                self.client.payees(book_id),
                self.client.account_balances(book_id),
            )

        self.executor.run(
            work, self._book_loaded, self._failed(_("Could not open the book"))
        )

    def _book_loaded(self, loaded) -> None:
        self.accounts, self.commodities, self.payees, subtree = loaded
        self.tree.blockSignals(True)
        self.tree.clear()
        items: dict[UUID, QTreeWidgetItem] = {}
        by_parent: dict[UUID | None, list[AccountOut]] = {}
        for a in self.accounts:
            by_parent.setdefault(a.parent_id, []).append(a)

        def add(parent_id: UUID | None, parent_item: QTreeWidgetItem | None) -> None:
            for a in sorted(by_parent.get(parent_id, []), key=lambda x: x.name.lower()):
                label = a.name + (" (closed)" if a.closed else "")
                amount = subtree.get(a.id)
                text = (
                    format_amount(amount, sign=natural_sign(a.account_class))
                    if amount
                    else ""
                )
                item = QTreeWidgetItem([label, text])
                if amount is not None:
                    # The minus sign is in the text; vermillion only repeats it.
                    brush = amount_brush(amount.minor * natural_sign(a.account_class))
                    if brush is not None:
                        item.setForeground(1, brush)
                item.setData(0, Qt.ItemDataRole.UserRole, a.id)
                item.setTextAlignment(
                    1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                items[a.id] = item
                add(a.id, item)

        add(None, None)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.register.setRowCount(0)
        self.register_title.setText(_("Choose an account"))
        if self.current_account is not None and self.current_account.id in items:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(items[self.current_account.id])
            self.tree.blockSignals(False)
            self._account_chosen(items[self.current_account.id])
        self._refresh_status()

    def _account_chosen(self, item: QTreeWidgetItem | None) -> None:
        if item is None or self.book is None:
            return
        account_id = item.data(0, Qt.ItemDataRole.UserRole)
        self.current_account = next(
            (a for a in self.accounts if a.id == account_id), None
        )
        self._enable_actions()
        if self.current_account is None:
            return
        book_id, account = self.book.id, self.current_account
        self.register_title.setText(f"{account.path} · {account.commodity}")
        self.executor.run(
            lambda: self.client.register(book_id, account.id, limit=500),
            self._register_loaded,
            self._failed(_("Could not read the register")),
        )

    def _register_loaded(self, page) -> None:
        account = self.current_account
        sign = natural_sign(account.account_class) if account else 1
        wanted, self._select_after_load = self._select_after_load, None
        payees = {p.id: p.name for p in self.payees}
        self.rows = list(page.items)
        self.register_opening_minor = (
            page.totals.opening.minor if page.totals.opening else 0
        )
        self.register.setRowCount(len(self.rows))
        for i, row in enumerate(self.rows):
            cells = [
                row.date.isoformat(),
                row.number,
                payees.get(row.payee_id, "") if row.payee_id else "",
                row.memo,
                RECONCILE_MARKS[row.reconcile_state.value],
                format_amount(row.quantity, sign=sign),
                format_amount(row.running_balance, sign=sign)
                if row.running_balance
                else "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 4:
                    # The one-letter column every ledger has had since paper.
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip(_(RECONCILE_LABELS[row.reconcile_state.value]))
                    if row.reconcile_state.value in ("RECONCILED", "FROZEN"):
                        item.setForeground(QBrush(QColor(ACCENT)))
                if col >= 5:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    # The minus sign is in the text; vermillion only repeats it.
                    signed = (
                        row.quantity.minor
                        if col == 5
                        else (row.running_balance.minor if row.running_balance else 0)
                    ) * sign
                    brush = amount_brush(signed)
                    if brush is not None:
                        item.setForeground(brush)
                self.register.setItem(i, col, item)
        closing = page.totals.closing
        if closing is not None and account is not None:
            self.register_title.setText(
                f"{account.path} · {_('balance')} {format_amount(closing, sign=sign)} {closing.commodity}"
            )
        if wanted is not None:
            # Arrived here from a search result: put it in front of the user.
            for i, row in enumerate(self.rows):
                if row.transaction_id == wanted:
                    self.register.selectRow(i)
                    cell = self.register.item(i, 0)
                    if cell is not None:
                        self.register.scrollToItem(cell)
                    break
        self._enable_actions()
        if wanted is not None and self._selected_row() is not None and self._can_write():
            self.edit_transaction()

    # -- reconciliation ---------------------------------------------------------------------
    def mark_selected(self, state: str) -> None:
        """Move the selected row's split to ``state``."""
        row = self._selected_row()
        if row is None or self.book is None or not self._can_write():
            return
        if state == "FROZEN" and not self._is_owner():
            return
        book_id = self.book.id
        wanted = ReconcileStateName(state)
        on = None if wanted is ReconcileStateName.NOT_RECONCILED else date.today()
        self.executor.run(
            lambda: self.client.set_split_state(
                book_id, row.transaction_id, row.split_id, wanted, on
            ),
            lambda _result: self._reload_book(),
            self._failed(_("Could not change the reconciliation state")),
        )

    def reconcile_account(self) -> None:
        """Tick the entries on a statement; mark them in one request."""
        account = self.current_account
        if account is None or self.book is None or not self._can_write():
            return
        from .reconcile import ReconcileDialog

        book_id = self.book.id
        dialog = ReconcileDialog(
            account,
            self.rows,
            {p.id: p.name for p in self.payees},
            opening_minor=self.register_opening_minor,
            can_freeze=self._is_owner(),
            parent=self,
        )
        if (
            self.run_dialog(dialog) != QDialog.DialogCode.Accepted
            or not dialog.selected_ids()
        ):
            return
        split_ids, state, statement_date = (
            dialog.selected_ids(),
            dialog.state(),
            dialog.statement_date(),
        )
        self.executor.run(
            lambda: self.client.reconcile_account(
                book_id, account.id, split_ids, state, statement_date
            ),
            lambda _result: self._reload_book(),
            self._failed(_("Could not reconcile")),
        )

    # -- search -------------------------------------------------------------------------
    def open_search(self) -> QMainWindow | None:
        """The search window for the current book; one at a time, brought to the front."""
        if self.book is None:
            return None
        from .search import open_search

        if self.search_window is not None and self.search_window.isVisible():
            self.search_window.raise_()
            self.search_window.activateWindow()
            return self.search_window
        self.search_window = open_search(
            self.client,
            self.book.id,
            self.accounts,
            self.payees,
            on_open=self.show_transaction,
            executor=self.executor,
            parent=self,
        )
        return self.search_window

    def show_transaction(self, transaction: TransactionOut) -> None:
        """Navigate to a transaction: its first account's register, the row
        selected -- and, for someone who may edit, the transaction opened."""
        if self.book is None or not transaction.splits:
            return
        account_id = transaction.splits[0].account_id
        items: dict[UUID, QTreeWidgetItem] = {}

        def collect(item: QTreeWidgetItem | None) -> None:
            if item is None:
                return
            items[item.data(0, Qt.ItemDataRole.UserRole)] = item
            for i in range(item.childCount()):
                collect(item.child(i))

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            collect(root.child(i))
        item = items.get(account_id)
        if item is None:
            return
        self._select_after_load = transaction.id
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)
        self._account_chosen(item)
        self.raise_()
        self.activateWindow()

    # -- writing -------------------------------------------------------------------------
    def _currency(self) -> CommodityOut | None:
        if self.book is None:
            return None
        return next(
            (c for c in self.commodities if c.id == self.book.base_currency_id), None
        )

    def _is_owner(self) -> bool:
        return self.book is not None and self.book.role == "owner"

    def _can_write(self) -> bool:
        return self.book is not None and self.book.role in ("editor", "owner")

    def _enable_actions(self) -> None:
        for action in self.report_actions:
            action.setEnabled(self.book is not None)
        self.search_button.setEnabled(self.book is not None)
        writable = self._can_write()
        self.new_button.setEnabled(
            writable
            and self.current_account is not None
            and not self.current_account.placeholder
            and not self.current_account.closed
        )
        selected = self.register.currentRow() >= 0 and self.register.currentRow() < len(
            self.rows
        )
        self.edit_button.setEnabled(writable and selected)
        self.delete_button.setEnabled(writable and selected)
        row = self._selected_row()
        frozen = row is not None and row.reconcile_state.value == "FROZEN"
        self.mark_button.setEnabled(writable and selected and not frozen)
        # FROZEN has no way back, so it is the owner's alone (the server checks too).
        self.mark_actions["FROZEN"].setVisible(self._is_owner())
        self.reconcile_button.setEnabled(
            writable
            and self.current_account is not None
            and not self.current_account.placeholder
        )

    def _selected_row(self) -> RegisterRowOut | None:
        index = self.register.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def new_transaction(self) -> None:
        currency = self._currency()
        if self.book is None or currency is None or self.current_account is None:
            return
        dialog = TransactionDialog(
            self.accounts,
            self.payees,
            currency,
            default_account_id=self.current_account.id,
            parent=self,
        )
        if self.run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        book_id, accounts = self.book.id, {a.id: a for a in self.accounts}
        draft = dialog.draft()

        def work():
            payee_id = draft.payee_id
            if draft.new_payee:
                payee_id = self.client.create_payee(
                    book_id, PayeeIn(name=draft.new_payee)
                ).id
            draft.payee_id = payee_id
            return self.client.post_transaction(
                book_id, draft.to_wire(accounts, currency)
            )

        self.executor.run(
            work, lambda _result: self._reload_book(), self._failed(_("Could not post"))
        )

    def edit_transaction(self) -> None:
        row = self._selected_row()
        currency = self._currency()
        if row is None or self.book is None or currency is None or not self._can_write():
            return
        book_id = self.book.id
        try:
            existing = self.client.transaction(book_id, row.transaction_id)
        except (ApiError, OfflineError) as exc:
            self._failed(_("Could not load the transaction"))(exc)
            return
        if existing.locked:
            QMessageBox.information(
                self,
                _("Locked"),
                _(
                    "A reconciled transaction cannot be changed; unreconcile its splits first."
                ),
            )
            return
        dialog = TransactionDialog(
            self.accounts, self.payees, currency, existing=existing, parent=self
        )
        if self.run_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        accounts = {a.id: a for a in self.accounts}
        draft = dialog.draft()

        def work():
            if draft.new_payee:
                draft.payee_id = self.client.create_payee(
                    book_id, PayeeIn(name=draft.new_payee)
                ).id
            return self.client.update_transaction(
                book_id, existing.id, draft.to_update(accounts, currency)
            )

        self.executor.run(
            work, lambda _result: self._reload_book(), self._failed(_("Could not save"))
        )

    def delete_transaction(self) -> None:
        row = self._selected_row()
        if row is None or self.book is None or not self._can_write():
            return
        if self.confirm(_("Delete this transaction?")) is not True:
            return
        book_id = self.book.id
        self.executor.run(
            lambda: self.client.delete_transaction(book_id, row.transaction_id),
            lambda _result: self._reload_book(),
            self._failed(_("Could not delete")),
        )

    def _reload_book(self) -> None:
        if self.book is not None:
            self._book_chosen(self.books.currentIndex())

    # -- reports ---------------------------------------------------------------------------
    def open_report(self, kind: str) -> QMainWindow | None:
        """A report window for the current book, in its own top-level window."""
        if self.book is None:
            return None
        from .reports import open_report

        window = open_report(
            self.client,
            self.book.id,
            kind,  # type: ignore[arg-type]
            self.commodities,
            self.book.base_currency_id,
            executor=self.executor,
            parent=self,
        )
        self.report_windows.append(window)
        return window

    def open_chart(self, kind: str) -> QMainWindow | None:
        """A graphical report for the current book, in its own top-level window."""
        if self.book is None:
            return None
        from .charts import open_chart

        window = open_chart(
            self.client,
            self.book.id,
            kind,  # type: ignore[arg-type]
            self.commodities,
            self.book.base_currency_id,
            executor=self.executor,
            parent=self,
        )
        self.report_windows.append(window)
        return window

    # -- sync and status --------------------------------------------------------------------
    def sync(self) -> None:
        def done(_: object) -> None:
            self._refresh_status()
            self._reload_book()

        self.executor.run(self.client.sync, done, self._failed(_("Sync failed")))

    def _refresh_status(self) -> None:
        status = self.client.status()
        parts = [_("Online") if status.online else _("Offline")]
        if status.mode == "offline":
            parts.append(_("local copy"))
            parts.append(
                _("{count} waiting for the server", count=status.queued)
                if status.queued
                else _("all sent")
            )
        else:
            parts.append(_("no local copy"))
        if status.last_error:
            parts.append(status.last_error)
        self.status_label.setText(" · ".join(parts))
        self.rejections_button.setVisible(bool(status.rejections))
        self.rejections_button.setText(
            _("{count} rejected…", count=len(status.rejections))
        )

    def show_rejections(self) -> None:
        status = self.client.status()
        lines = [f"{r.command.summary}: {r.message}" for r in status.rejections]
        QMessageBox.warning(
            self,
            _("The server refused"),
            _(
                "These changes were made here while offline, but the server -- which has the final say -- "
                "refused them on replay. The local copy has been rebuilt from the server; nothing was merged."
            )
            + "\n\n"
            + "\n".join(lines),
        )
        dismiss = getattr(self.client, "dismiss_rejections", None)
        if dismiss:
            dismiss()
        self._refresh_status()

    # -- hooks a test can replace -----------------------------------------------------------
    def run_dialog(self, dialog: QDialog) -> int:
        return dialog.exec()

    def confirm(self, question: str) -> bool:
        answer = QMessageBox.question(self, "PyMoney", question)
        return answer == QMessageBox.StandardButton.Yes

    def _failed(self, title: str) -> Callable[[Exception], None]:
        def handle(exc: Exception) -> None:
            self.last_error = (title, exc)
            if isinstance(exc, (ApiError, OfflineError)):
                self.report_error(title, str(exc))
            else:
                self.report_error(
                    title, "".join(traceback.format_exception_only(type(exc), exc))
                )
            self._refresh_status()

        return handle

    def report_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

"""Finding a transaction: the search window.

Every criterion narrows and they combine with AND -- text in a memo or number,
an amount or a range of amounts, a date range, an account (with or without its
sub-accounts), a payee. The server (or, offline, the replica) answers newest
first, at most ``limit``; when it says there were more, the window says so and
the user narrows. A result is opened by double-click or Enter, and the main
window takes it from there: it shows the account's register with the row
selected and, for someone who may edit, opens the transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from pymoney.api.schemas import AccountOut, PayeeOut, TransactionOut
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import LedgerClient, SearchCriteria
from ..i18n import _
from .main_window import Executor, ThreadedExecutor, format_amount

__all__ = ["SearchWindow", "describe_accounts", "open_search"]


def describe_accounts(
    transaction: TransactionOut, accounts: dict[UUID, AccountOut]
) -> str:
    """``Checking → Groceries``: where the money came from and where it went."""
    froms = [s for s in transaction.splits if s.value.minor < 0]
    tos = [s for s in transaction.splits if s.value.minor > 0]

    def names(splits: list[Any]) -> str:
        seen: list[str] = []
        for s in splits:
            account = accounts.get(s.account_id)
            name = account.name if account else "?"
            if name not in seen:
                seen.append(name)
        return ", ".join(seen)

    if froms and tos:
        return f"{names(froms)} → {names(tos)}"
    return names(list(transaction.splits))


class SearchWindow(QMainWindow):
    """Criteria above, results below; every result opens in the main window."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        accounts: list[AccountOut],
        payees: list[PayeeOut],
        *,
        on_open: Callable[[TransactionOut], None] | None = None,
        executor: Executor | None = None,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.accounts = {a.id: a for a in accounts}
        self.payees = {p.id: p for p in payees}
        self.on_open = on_open or (lambda transaction: None)
        self.executor = executor or ThreadedExecutor()
        self.today = today or date.today()
        self.results: list[TransactionOut] = []
        self.has_more = False
        self.last_error: Exception | None = None
        title = _("Find transactions")
        self.setWindowTitle(f"PyMoney — {title}")
        self.resize(900, 600)

        # -- criteria --------------------------------------------------------
        form = QFormLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText(_("A word from the memo, or a cheque number"))
        form.addRow(_("Text"), self.text)

        amounts = QHBoxLayout()
        self.amount = QLineEdit()
        self.amount.setPlaceholderText(_("exactly"))
        self.amount.setMaximumWidth(110)
        self.amount_min = QLineEdit()
        self.amount_min.setPlaceholderText(_("from"))
        self.amount_min.setMaximumWidth(110)
        self.amount_max = QLineEdit()
        self.amount_max.setPlaceholderText(_("to"))
        self.amount_max.setMaximumWidth(110)
        amounts.addWidget(self.amount)
        amounts.addWidget(QLabel(_("or between")))
        amounts.addWidget(self.amount_min)
        amounts.addWidget(QLabel("-"))
        amounts.addWidget(self.amount_max)
        amounts.addWidget(QLabel(_("(the size of a leg, in its own currency)")))
        amounts.addStretch()
        form.addRow(_("Amount"), amounts)

        dates = QHBoxLayout()
        self.use_start = QCheckBox(_("from"))
        self.start = QDateEdit(QDate(self.today.year, 1, 1))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd")
        self.start.setEnabled(False)
        self.use_end = QCheckBox(_("to"))
        self.end = QDateEdit(QDate(self.today.year, self.today.month, self.today.day))
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("yyyy-MM-dd")
        self.end.setEnabled(False)
        self.use_start.toggled.connect(self.start.setEnabled)
        self.use_end.toggled.connect(self.end.setEnabled)
        for widget in (self.use_start, self.start, self.use_end, self.end):
            dates.addWidget(widget)
        dates.addStretch()
        form.addRow(_("Dates"), dates)

        where = QHBoxLayout()
        self.account = QComboBox()
        self.account.addItem(_("Any account"), "")
        for account in sorted(accounts, key=lambda a: a.path.lower()):
            self.account.addItem(account.path, str(account.id))
        self.subtree = QCheckBox(_("Include sub-accounts"))
        where.addWidget(self.account, 1)
        where.addWidget(self.subtree)
        form.addRow(_("Account"), where)

        self.payee = QComboBox()
        self.payee.addItem(_("Any payee"), "")
        for payee in sorted(payees, key=lambda p: p.name.lower()):
            self.payee.addItem(payee.name, str(payee.id))
        form.addRow(_("Payee"), self.payee)

        buttons = QHBoxLayout()
        self.search_button = QPushButton(_("Search"))
        self.search_button.setDefault(True)
        self.search_button.clicked.connect(self.search)
        self.clear_button = QPushButton(_("Clear"))
        self.clear_button.clicked.connect(self.clear)
        buttons.addStretch()
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.search_button)

        # -- results -----------------------------------------------------------
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [_("Date"), _("Payee"), _("Memo"), _("Accounts"), _("Amount")]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _index: self.open_selected())
        self.open_button = QPushButton(_("Open"))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(
            lambda: self.open_button.setEnabled(self.selected() is not None)
        )
        QShortcut(QKeySequence(Qt.Key.Key_Return), self.table, self.open_selected)

        self.status = QLabel(_("Fill in one or more criteria and search."))
        self.status.setStyleSheet("color: gray")
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.open_button)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addLayout(footer)
        self.setCentralWidget(central)
        for field in (self.text, self.amount, self.amount_min, self.amount_max):
            field.returnPressed.connect(self.search)
        self.text.setFocus()

    # -- criteria ----------------------------------------------------------------
    def criteria(self) -> SearchCriteria:
        def text_or_none(field: QLineEdit) -> str | None:
            value = field.text().strip().replace(",", ".")
            return value or None

        def pydate(edit: QDateEdit) -> date:
            q = edit.date()
            return date(q.year(), q.month(), q.day())

        account = self.account.currentData()
        payee = self.payee.currentData()
        return SearchCriteria(
            text=self.text.text().strip() or None,
            amount=text_or_none(self.amount),
            amount_min=text_or_none(self.amount_min),
            amount_max=text_or_none(self.amount_max),
            start=pydate(self.start) if self.use_start.isChecked() else None,
            end=pydate(self.end) if self.use_end.isChecked() else None,
            account_id=UUID(account) if account else None,
            include_children=self.subtree.isChecked(),
            payee_id=UUID(payee) if payee else None,
        )

    def clear(self) -> None:
        for field in (self.text, self.amount, self.amount_min, self.amount_max):
            field.clear()
        self.use_start.setChecked(False)
        self.use_end.setChecked(False)
        self.account.setCurrentIndex(0)
        self.subtree.setChecked(False)
        self.payee.setCurrentIndex(0)
        self._show([], False)
        self.status.setText(_("Fill in one or more criteria and search."))

    # -- searching -------------------------------------------------------------------
    def search(self) -> None:
        criteria = self.criteria()
        if criteria.is_empty:
            self.status.setText(_("Fill in at least one criterion."))
            return
        self.status.setText(_("Searching…"))
        client, book_id = self.client, self.book_id
        self.executor.run(
            lambda: client.search(book_id, criteria), self._loaded, self._failed
        )

    def _failed(self, exc: Exception) -> None:
        self.last_error = exc
        self._show([], False)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setText(f"<span style='color:#bc4700'>{exc}</span>")

    def _loaded(self, result: Any) -> None:
        self.last_error = None
        self._show(list(result.items), result.has_more)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        if result.has_more:
            self.status.setText(
                _(
                    "More than {n} transactions match; showing the newest. Narrow the search.",
                    n=len(result.items),
                )
            )
        elif not result.items:
            self.status.setText(_("Nothing matches."))
        elif len(result.items) == 1:
            self.status.setText(_("One transaction matches."))
        else:
            self.status.setText(_("{n} transactions match.", n=len(result.items)))

    def _show(self, results: list[TransactionOut], has_more: bool) -> None:
        self.results = results
        self.has_more = has_more
        table = self.table
        table.setRowCount(len(results))
        for i, t in enumerate(results):
            payee = self.payees.get(t.payee_id) if t.payee_id else None
            debits = sum(s.value.minor for s in t.splits if s.value.minor > 0)
            total = (
                t.splits[0].value.model_copy(update={"minor": debits})
                if t.splits
                else None
            )
            cells = [
                t.date.isoformat(),
                payee.name if payee else "",
                t.memo,
                describe_accounts(t, self.accounts),
                f"{format_amount(total)} {t.currency}" if total else "",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 4:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(i, col, item)
        table.resizeColumnsToContents()
        header = table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.open_button.setEnabled(False)

    # -- opening a result -----------------------------------------------------------------
    def selected(self) -> TransactionOut | None:
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        (row,) = rows
        return self.results[row] if 0 <= row < len(self.results) else None

    def open_selected(self) -> None:
        transaction = self.selected()
        if transaction is not None:
            self.on_open(transaction)


def open_search(
    client: LedgerClient,
    book_id: UUID,
    accounts: list[AccountOut],
    payees: list[PayeeOut],
    *,
    on_open: Callable[[TransactionOut], None] | None = None,
    executor: Executor | None = None,
    parent: QWidget | None = None,
) -> SearchWindow:
    """Make, show and return a search window; kept alive by its parent."""
    window = SearchWindow(
        client,
        book_id,
        accounts,
        payees,
        on_open=on_open,
        executor=executor,
        parent=parent,
    )
    window.setWindowFlag(Qt.WindowType.Window, True)
    window.show()
    return window

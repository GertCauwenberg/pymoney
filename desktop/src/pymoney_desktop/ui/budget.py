"""The budget view: what was spent beside what was allowed.

A table of budgetable accounts down, the months across. A plan holds a figure
per month and nothing more. The *Monthly* column is a screen convenience: it
fills the months on screen in one go, and shows a figure only when they already
agree. Each month cell shows ``spent`` over ``remaining`` for that account's
subtree, exactly the figures the budget report returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from pymoney.api.schemas import AmountOut, BudgetMonthsIn, BudgetTableOut
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import ApiError, LedgerClient, OfflineError
from ..i18n import _

__all__ = ["BudgetWindow", "open_budget"]

_ACCOUNT_ROLE = 256


def _minor_to_text(minor: int, scale: int) -> str:
    digits = len(str(scale)) - 1 if scale in (1, 10, 100, 1000, 10000) else 0
    sign = "-" if minor < 0 else ""
    s = str(abs(minor)).rjust(digits + 1, "0")
    return f"{sign}{s[:-digits]}.{s[-digits:]}" if digits else f"{sign}{s}"


def _monthly_figure(budget: Sequence[AmountOut | None]) -> str:
    """The "same every month" figure for the period on screen.

    Shown only when every month on screen already agrees: the cell fills them
    all, so a figure here means "each of these", and it cannot mean that while
    they differ.  All blank is blank, which is what "nothing planned" says.
    """
    minors = {None if b is None else b.minor for b in budget}
    if len(minors) != 1:
        return ""
    first = next((b for b in budget if b is not None), None)
    return "" if first is None else _minor_to_text(first.minor, first.scale)


class BudgetWindow(QMainWindow):
    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        *,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.today = today or date.today()
        self.setWindowTitle(f"PyMoney — {_('Budget')}")
        self.resize(900, 560)
        self._loading = False
        self._months: list[str] = []
        """The period on screen: what the Monthly cell fills and clears."""

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(_("From")))
        self.start = QDateEdit(self._year_start())
        self.start.setDisplayFormat("yyyy-MM")
        self.start.setCalendarPopup(True)
        self.start.dateChanged.connect(lambda *_: self.reload())
        bar.addWidget(self.start)
        bar.addWidget(QLabel(_("To")))
        self.end = QDateEdit(self._month_end())
        self.end.setDisplayFormat("yyyy-MM")
        self.end.setCalendarPopup(True)
        self.end.dateChanged.connect(lambda *_: self.reload())
        bar.addWidget(self.end)
        bar.addStretch()
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        bar.addWidget(self.status)
        layout.addLayout(bar)

        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.table.cellChanged.connect(self._cell_changed)
        layout.addWidget(self.table)
        self.reload()

    # -- data ---------------------------------------------------------------
    def _year_start(self):
        from PySide6.QtCore import QDate

        return QDate(self.today.year, 1, 1)

    def _month_end(self):
        from PySide6.QtCore import QDate

        return QDate(self.today.year, self.today.month, 1)

    def _range(self) -> tuple[date, date]:
        s, e = self.start.date(), self.end.date()
        start = date(s.year(), s.month(), 1)
        last_day = 28
        for d in (31, 30, 29, 28):
            try:
                date(e.year(), e.month(), d)
                last_day = d
                break
            except ValueError:
                continue
        return start, date(e.year(), e.month(), last_day)

    def reload(self) -> None:
        if self._loading:
            return
        start, end = self._range()
        try:
            report: BudgetTableOut = self.client.budget_report(
                self.book_id, start=start, end=end
            )
        except (ApiError, OfflineError) as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("")
        self._fill(report)

    def _fill(self, report: BudgetTableOut) -> None:
        self._loading = True
        columns = report.period.columns
        self._months = [c.label for c in columns]
        headers = [_("Account"), _("Monthly"), *(c.label for c in columns), _("Total")]
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        # Every row shows the rolled-up plan; the Monthly cell is editable only
        # where the account can take a plan of its own.
        by_account = {r.account_id: r for r in report.rows}
        rows = list(report.period.rows)
        self.table.setRowCount(len(rows))
        for i, prow in enumerate(rows):
            brow = by_account.get(prow.account_id)
            # A placeholder only groups its children and a closed account takes
            # nothing new, so the server refuses a plan on either: the row still
            # shows the rolled-up plan, but there is nothing to type into.
            budgetable = brow is not None and not brow.placeholder and not brow.closed
            name = QTableWidgetItem("  " * prow.depth + prow.path.split(":")[-1])
            name.setData(_ACCOUNT_ROLE, str(prow.account_id))
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 0, name)

            monthly = QTableWidgetItem(_monthly_figure(brow.budget if brow else ()))
            if not budgetable:
                monthly.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 1, monthly)

            for j, _col in enumerate(columns):
                spent = prow.display_cells[j]
                # The server carries plans and movement only, so what is left
                # is budget minus the row's own movement (see BudgetTableOut).
                plan = brow.budget[j] if brow is not None else None
                text = _minor_to_text(spent.minor, spent.scale)
                if plan is not None:
                    left = _("{amount} left").format(
                        amount=_minor_to_text(plan.minor - spent.minor, plan.scale)
                    )
                    text += f"  ·  {left}"
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(i, 2 + j, item)

            total = prow.display_total
            titem = QTableWidgetItem(_minor_to_text(total.minor, total.scale))
            titem.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, len(headers) - 1, titem)

        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._loading = False

    # -- editing ------------------------------------------------------------
    def _cell_changed(self, row: int, col: int) -> None:
        if self._loading or col != 1:
            return
        name = self.table.item(row, 0)
        if name is None:
            return
        account_id = UUID(name.data(_ACCOUNT_ROLE))
        monthly = self.table.item(row, 1)
        text = (monthly.text() if monthly is not None else "").strip()
        try:
            # The Monthly cell fills the months on screen and nothing else: a
            # plan holds months, so it cannot reach beyond what is showing.
            fill = text or None
            self.client.set_budget_months(
                self.book_id,
                account_id,
                BudgetMonthsIn(months=dict.fromkeys(self._months, fill)),
            )
        except (ApiError, OfflineError) as exc:
            self.status.setText(str(exc))
        self.reload()


def open_budget(
    client: LedgerClient,
    book_id: UUID,
    *,
    parent: QWidget | None = None,
) -> BudgetWindow:
    window = BudgetWindow(client, book_id, parent=parent)
    window.setWindowFlag(Qt.WindowType.Window, True)
    window.show()
    return window

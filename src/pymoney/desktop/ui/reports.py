"""The three reports, with the options the web app offers.

One window class, three report kinds. The top is the valuation policy -- date
(or period), reporting currency, closed accounts, strict -- because a report is
a function of its policy (decision #12): the same settings against the same
book give the same numbers, on the desktop as on the web, since both run the
same server-side view (or, offline, the same view code on the replica).

Below the rows, every report shows its working: the exchange rates it used,
with their sources and, for chained rates, the path; and in lenient mode what
it could not value and left out. A number without that is a number to take on
trust, which a report should not ask for.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...api.schemas import (
    AmountOut,
    BalanceSheetOut,
    CommodityOut,
    ConversionOut,
    HoldingsOut,
    IncomeStatementOut,
    ReportRowOut,
)
from ..client import LedgerClient, ReportOptions
from .main_window import Executor, ThreadedExecutor, format_amount

__all__ = [
    "PERIOD_PRESETS",
    "ReportKind",
    "ReportWindow",
    "open_report",
    "period_for",
    "rows_to_csv",
]

ReportKind = Literal["balance-sheet", "income-statement", "holdings"]
TITLES: dict[str, str] = {
    "balance-sheet": "Balance sheet",
    "income-statement": "Income statement",
    "holdings": "Holdings",
}

# The same presets as the web app (packages/core/src/periods.ts), so a period
# means the same thing wherever it is chosen.
PERIOD_PRESETS: tuple[tuple[str, str], ...] = (
    ("this-month", "This month"),
    ("last-month", "Last month"),
    ("this-quarter", "This quarter"),
    ("last-quarter", "Last quarter"),
    ("year-to-date", "Year to date"),
    ("last-year", "Last year"),
    ("last-12-months", "Last 12 months"),
)


def _month_end(year: int, month: int) -> date:
    first_next = date(year + (month // 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def period_for(preset: str, today: date) -> tuple[date, date]:
    y, m = today.year, today.month
    quarter_start = ((m - 1) // 3) * 3 + 1
    if preset == "this-month":
        return date(y, m, 1), today
    if preset == "last-month":
        y2, m2 = (y - 1, 12) if m == 1 else (y, m - 1)
        return date(y2, m2, 1), _month_end(y2, m2)
    if preset == "this-quarter":
        return date(y, quarter_start, 1), today
    if preset == "last-quarter":
        start_month = quarter_start - 3
        y2 = y if start_month >= 1 else y - 1
        start_month = start_month if start_month >= 1 else start_month + 12
        return date(y2, start_month, 1), _month_end(y2, start_month + 2)
    if preset == "year-to-date":
        return date(y, 1, 1), today
    if preset == "last-year":
        return date(y - 1, 1, 1), date(y - 1, 12, 31)
    if preset == "last-12-months":
        try:
            start = date(y - 1, m, today.day)
        except ValueError:  # 29 February
            start = date(y - 1, m, 28)
        return start, today
    raise ValueError(f"unknown period preset {preset!r}")


def rows_to_csv(rows: list[ReportRowOut]) -> str:
    """The same columns as the web app's CSV export, so the two files agree."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["path", "depth", "value", "own_value", "commodity"])
    for r in rows:
        writer.writerow(
            [
                r.path,
                r.depth,
                format_amount(r.display_value).replace(",", ""),
                format_amount(r.own_value).replace(",", ""),
                r.display_value.commodity,
            ]
        )
    return out.getvalue()


def holdings_to_csv(report: HoldingsOut) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(
        [
            "path",
            "commodity",
            "quantity",
            "unit_price",
            "cost",
            "market_value",
            "unrealised_gain",
            "target",
        ]
    )
    for p in report.positions:
        writer.writerow(
            [
                p.path,
                p.commodity,
                format_amount(p.quantity).replace(",", ""),
                ""
                if p.unit_price is None
                else p.unit_price.approx.rstrip("0").rstrip("."),
                format_amount(p.cost).replace(",", ""),
                format_amount(p.market_value).replace(",", ""),
                ""
                if p.unrealised_gain is None
                else format_amount(p.unrealised_gain).replace(",", ""),
                report.target,
            ]
        )
    return out.getvalue()


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class ReportWindow(QMainWindow):
    """One report, live: every option change re-runs it."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        kind: ReportKind,
        commodities: list[CommodityOut],
        base_currency_id: UUID | None,
        *,
        executor: Executor | None = None,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.kind: ReportKind = kind
        self.executor = executor or ThreadedExecutor()
        self.today = today or date.today()
        self.setWindowTitle(f"PyMoney — {TITLES[kind]}")
        self.resize(900, 650)
        self.report: BalanceSheetOut | IncomeStatementOut | HoldingsOut | None = None
        self.last_error: Exception | None = None
        self._loading = False
        self._settling = False  # True while a preset is writing both dates

        currencies = sorted(
            (c for c in commodities if c.kind == "CURRENCY"), key=lambda c: c.code
        )
        base = next((c for c in commodities if c.id == base_currency_id), None)

        # -- the policy ------------------------------------------------------
        controls = QHBoxLayout()
        self.preset = QComboBox()
        self.start = QDateEdit(_qdate(period_for("this-month", self.today)[0]))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd")
        self.as_of = QDateEdit(_qdate(self.today))
        self.as_of.setCalendarPopup(True)
        self.as_of.setDisplayFormat("yyyy-MM-dd")
        if kind == "income-statement":
            self.preset.addItem("Custom period", "")
            for key, label in PERIOD_PRESETS:
                self.preset.addItem(label, key)
            self.preset.setCurrentIndex(1)
            controls.addWidget(QLabel("Period"))
            controls.addWidget(self.preset)
            controls.addWidget(QLabel("From"))
            controls.addWidget(self.start)
            controls.addWidget(QLabel("To"))
        else:
            controls.addWidget(QLabel("As of"))
        controls.addWidget(self.as_of)

        self.target = QComboBox()
        self.target.addItem(
            f"{base.code} (book currency)" if base else "Book currency", ""
        )
        for c in currencies:
            if base is None or c.id != base.id:
                self.target.addItem(
                    c.code + (f" — {c.name}" if c.name and c.name != c.code else ""),
                    str(c.id),
                )
        controls.addWidget(QLabel("Report in"))
        controls.addWidget(self.target)

        self.closed = QCheckBox("Include closed accounts")
        self.empty = QCheckBox("Include empty positions")
        self.strict = QCheckBox("Fail if anything cannot be valued")
        self.strict.setToolTip("Off: list it below and leave it out of the totals.")
        if kind == "holdings":
            controls.addWidget(self.empty)
        else:
            controls.addWidget(self.closed)
        controls.addWidget(self.strict)
        controls.addStretch()
        self.export_button = QPushButton("Export CSV")
        self.export_button.clicked.connect(self.export_csv)
        controls.addWidget(self.export_button)

        # -- the rows ----------------------------------------------------------
        self.tree = QTreeWidget()
        if kind == "holdings":
            self.tree.setHeaderLabels(
                [
                    "Position",
                    "Commodity",
                    "Quantity",
                    "Price",
                    "Cost",
                    "Market value",
                    "Gain",
                ]
            )
        else:
            self.tree.setHeaderLabels(
                ["Account", "Own", "Amount" if kind == "income-statement" else "Balance"]
            )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setAlternatingRowColors(True)

        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        self.provenance = QLabel()
        self.provenance.setWordWrap(True)
        self.provenance.setTextFormat(Qt.TextFormat.RichText)
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(controls)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.totals)
        layout.addWidget(self.provenance)
        layout.addWidget(self.status)
        self.setCentralWidget(central)

        # -- wiring: any change re-runs the report ---------------------------
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        self.start.dateChanged.connect(lambda _: self._custom_period())
        self.as_of.dateChanged.connect(lambda _: self._custom_period())
        for widget in (self.target,):
            widget.currentIndexChanged.connect(lambda _: self.refresh())
        for box in (self.closed, self.empty, self.strict):
            box.toggled.connect(lambda _: self.refresh())
        if kind == "income-statement":
            self._preset_chosen(self.preset.currentIndex())
        else:
            self.refresh()

    # -- options -----------------------------------------------------------------
    def _preset_chosen(self, index: int) -> None:
        key = self.preset.itemData(index)
        if key:
            start, end = period_for(key, self.today)
            self._settling = True
            self.start.setDate(_qdate(start))
            self.as_of.setDate(_qdate(end))
            self._settling = False
        self.refresh()

    def _custom_period(self) -> None:
        if self._settling:
            return
        if self.kind == "income-statement" and self.preset.currentData():
            # The dates were edited by hand: this is no preset any more.
            self.preset.blockSignals(True)
            self.preset.setCurrentIndex(0)
            self.preset.blockSignals(False)
        self.refresh()

    def options(self) -> ReportOptions:
        chosen = self.target.currentData()
        return ReportOptions(
            as_of=_pydate(self.as_of.date()),
            target_id=UUID(chosen) if chosen else None,
            include_closed=self.closed.isChecked(),
            strict=self.strict.isChecked(),
        )

    # -- running -------------------------------------------------------------------
    def refresh(self) -> None:
        options = self.options()
        start = _pydate(self.start.date())
        if self.kind == "income-statement" and start > (options.as_of or self.today):
            self.status.setText("The period ends before it starts.")
            return
        self.status.setText("Loading…")
        self._loading = True
        kind, client, book_id = self.kind, self.client, self.book_id

        def work() -> Any:
            if kind == "balance-sheet":
                return client.balance_sheet(book_id, options)
            if kind == "income-statement":
                return client.income_statement(book_id, start=start, options=options)
            return client.holdings(book_id, options, include_empty=self.empty.isChecked())

        self.executor.run(work, self._loaded, self._failed)

    def _failed(self, exc: Exception) -> None:
        self._loading = False
        self.last_error = exc
        self.report = None
        self.tree.clear()
        self.totals.setText("")
        self.provenance.setText("")
        self.status.setText(f"<span style='color:#b91c1c'>{exc}</span>")
        self.status.setTextFormat(Qt.TextFormat.RichText)

    def _loaded(self, report: Any) -> None:
        self._loading = False
        self.last_error = None
        self.report = report
        self.tree.clear()
        if isinstance(report, HoldingsOut):
            self._show_holdings(report)
        else:
            self._show_rows(report)
        self._show_provenance(report)
        self.status.setText(f"{TITLES[self.kind]} in {report.target}")

    # -- rendering -------------------------------------------------------------------
    def _show_rows(self, report: BalanceSheetOut | IncomeStatementOut) -> None:
        stack: list[QTreeWidgetItem] = []
        for row in report.rows:
            own = row.own_value
            # `own_value` comes as stored; give it the sign `display_value` has.
            if (row.value.minor != 0 and row.display_value.minor == -row.value.minor) or (
                row.value.minor == 0 and _credit_normal(row, report)
            ):
                own = replace_sign(own)
            has_children = row.own_value.minor != row.value.minor
            item = QTreeWidgetItem(
                [
                    row.path.rsplit(":", 1)[-1],
                    # As on the web: a parent's own balance, quiet when zero.
                    format_amount(own) if has_children and own.minor != 0 else "",
                    format_amount(row.display_value),
                ]
            )
            item.setTextAlignment(
                1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            item.setTextAlignment(
                2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            item.setToolTip(0, row.path)
            item.setData(0, Qt.ItemDataRole.UserRole, str(row.account_id))
            del stack[row.depth :]
            if stack:
                stack[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append(item)
        self.tree.expandAll()
        if isinstance(report, BalanceSheetOut):
            self.totals.setText(
                f"Total assets <b>{format_amount(report.assets)} {report.target}</b> &nbsp;·&nbsp; "
                f"Total liabilities <b>{format_amount(report.liabilities_owed)} {report.target}</b> &nbsp;·&nbsp; "
                f"Net worth <b>{format_amount(report.net_worth)} {report.target}</b>"
            )
        else:
            rate = ""
            if report.savings_rate is not None:
                pct = (
                    report.savings_rate.num * 1000 + report.savings_rate.den // 2
                ) // report.savings_rate.den
                rate = f" &nbsp;·&nbsp; Savings rate <b>{pct / 10:.1f}&nbsp;%</b>"
            self.totals.setText(
                f"Income <b>{format_amount(report.income_earned)} {report.target}</b> &nbsp;·&nbsp; "
                f"Expenses <b>{format_amount(report.expenses)} {report.target}</b> &nbsp;·&nbsp; "
                f"Net <b>{format_amount(report.net)} {report.target}</b>{rate}"
            )

    def _show_holdings(self, report: HoldingsOut) -> None:
        for p in report.positions:
            item = QTreeWidgetItem(
                [
                    p.path,
                    p.commodity,
                    format_amount(p.quantity),
                    ""
                    if p.unit_price is None
                    else p.unit_price.approx.rstrip("0").rstrip("."),
                    format_amount(p.cost),
                    format_amount(p.market_value),
                    "" if p.unrealised_gain is None else format_amount(p.unrealised_gain),
                ]
            )
            for col in range(2, 7):
                item.setTextAlignment(
                    col, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            item.setData(0, Qt.ItemDataRole.UserRole, str(p.account_id))
            self.tree.addTopLevelItem(item)
        self.totals.setText(
            f"Total value <b>{format_amount(report.total_value)} {report.target}</b> &nbsp;·&nbsp; "
            f"Total cost <b>{format_amount(report.total_cost)} {report.target}</b>"
        )

    def _show_provenance(
        self, report: BalanceSheetOut | IncomeStatementOut | HoldingsOut
    ) -> None:
        lines: list[str] = []
        conversions: list[ConversionOut] = getattr(report, "conversions", [])
        if conversions:
            lines.append("<b>Rates used</b>")
            for c in conversions:
                path = " → ".join(c.path)
                lines.append(
                    f"1 {c.base} = {c.rate.approx.rstrip('0').rstrip('.')} {c.quote} &nbsp;<span style='color:gray'>"
                    f"({path}; {', '.join(c.sources)}{'; ' + str(c.hops) + ' hops' if c.hops > 1 else ''})</span>"
                )
        unpriced: list[str] = getattr(report, "unpriced", [])
        if unpriced:
            lines.append(
                f"<b style='color:#b45309'>Could not be valued and left out:</b> {', '.join(unpriced)}"
                " — add a price, or turn strict mode on to make this an error."
            )
        self.provenance.setText("<br>".join(lines))

    # -- export ----------------------------------------------------------------------
    def csv_text(self) -> str:
        if self.report is None:
            return ""
        if isinstance(self.report, HoldingsOut):
            return holdings_to_csv(self.report)
        return rows_to_csv(list(self.report.rows))

    def export_csv(self) -> None:
        if self.report is None:
            return
        suffix = _pydate(self.as_of.date()).isoformat()
        path, _ = self.choose_path(f"{self.kind}-{suffix}.csv")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(self.csv_text())
            self.status.setText(f"Saved {path}")

    def choose_path(self, suggested: str) -> tuple[str, str]:
        """A hook a test can replace."""
        return QFileDialog.getSaveFileName(self, "Export CSV", suggested, "CSV (*.csv)")


def replace_sign(amount: AmountOut) -> AmountOut:
    return replace_amount(amount, -amount.minor)


def replace_amount(amount: AmountOut, minor: int) -> AmountOut:
    return amount.model_copy(update={"minor": minor})


def _credit_normal(
    row: ReportRowOut, report: BalanceSheetOut | IncomeStatementOut
) -> bool:
    """Whether a zero-valued row belongs to a credit-normal tree: the income
    statement's income side, or liabilities and equity on the balance sheet."""
    top = row.path.split(":", 1)[0].lower()
    if isinstance(report, IncomeStatementOut):
        return top.startswith("income")
    return top.startswith(("liabilit", "equity"))


def open_report(
    client: LedgerClient,
    book_id: UUID,
    kind: ReportKind,
    commodities: list[CommodityOut],
    base_currency_id: UUID | None,
    *,
    executor: Executor | None = None,
    parent: QWidget | None = None,
) -> ReportWindow:
    """Make, show and return a report window; kept alive by its parent."""
    window = ReportWindow(
        client,
        book_id,
        kind,
        commodities,
        base_currency_id,
        executor=executor,
        parent=parent,
    )
    window.setWindowFlag(Qt.WindowType.Window, True)
    window.show()
    return window

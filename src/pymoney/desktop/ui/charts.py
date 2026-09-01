"""The graphical reports: pies, the net-worth line, and the period table.

Five more views on the same figures the three classic reports produce -- the
income statement sliced into a pie, the holdings sliced into a pie, the balance
sheet's totals drawn over time, and income and expenses spread across the
months of a period. Nothing is computed here that a report does not already
compute: a pie slice is a report row's own value, a point on the line is a
balance sheet, a cell in the table is one month of the income statement. So
the numbers agree with the tables, on the desktop as on the web, because they
are the same numbers.

One window class again, because the controls are the same: a period (or a date,
for holdings), the reporting currency, strictness, and for the two time-based
reports the interval. Every change re-runs the report.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QDateTimeAxis,
    QLineSeries,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import QDateTime, QPointF, Qt
from PySide6.QtGui import QColor, QPainter
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...api.schemas import (
    AccountOut,
    AmountOut,
    CommodityOut,
    HoldingsOut,
    IncomeStatementOut,
    NetWorthSeriesOut,
    PeriodTableOut,
    ReportInterval,
)
from ..client import LedgerClient, ReportOptions
from ..i18n import _
from .main_window import Executor, ThreadedExecutor, format_amount
from .reports import PERIOD_PRESETS, _pydate, _qdate, period_for
from .theme import CHART_PALETTE

__all__ = ["CHART_TITLES", "ChartKind", "ChartWindow", "open_chart", "pie_slices"]

ChartKind = Literal[
    "income-pie", "expense-pie", "holdings-pie", "net-worth", "period-table"
]
CHART_TITLES: dict[str, str] = {
    "income-pie": "Income by category",
    "expense-pie": "Expenses by category",
    "holdings-pie": "Holdings by position",
    "net-worth": "Net worth over time",
    "period-table": "Income and expenses by period",
}
INTERVALS: tuple[tuple[str, str], ...] = (
    ("month", "Monthly"),
    ("quarter", "Quarterly"),
    ("year", "Yearly"),
)


def _msecs(day: date) -> float:
    """A date at UTC midnight, as the milliseconds QDateTimeAxis plots."""
    stamp = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return stamp.timestamp() * 1000.0


OTHER_BELOW = 0.02
"""Slices smaller than this share are gathered into one "Other" slice: a pie
with forty hairline wedges tells nobody anything."""


def pie_slices(
    labelled: Sequence[tuple[str, int]], *, other_label: str = "Other"
) -> list[tuple[str, int]]:
    """Sort slices by size and fold the slivers into one.

    Only positive figures are slices: a category whose net for the period is a
    refund has no share of "what the money went on", and is left out rather
    than drawn as a negative wedge.
    """
    positive = sorted(((n, v) for n, v in labelled if v > 0), key=lambda s: -s[1])
    total = sum(v for _, v in positive)
    if total == 0:
        return []
    big = [(n, v) for n, v in positive if v / total >= OTHER_BELOW]
    rest = sum(v for n, v in positive if v / total < OTHER_BELOW)
    if rest and len(big) < len(positive) - 1:
        big.append((other_label, rest))
    elif rest:
        big = positive
    return big


class ChartWindow(QMainWindow):
    """One graphical report, live: every option change re-runs it."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        kind: ChartKind,
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
        self.kind: ChartKind = kind
        self.executor = executor or ThreadedExecutor()
        self.today = today or date.today()
        self.setWindowTitle(f"PyMoney — {_(CHART_TITLES[kind])}")
        self.resize(900, 650)
        self.report: Any = None
        self.last_error: Exception | None = None
        self.slices: list[tuple[str, int]] = []
        """What the pie shows: (label, minor units), largest first."""
        self._settling = False

        currencies = sorted(
            (c for c in commodities if c.kind == "CURRENCY"), key=lambda c: c.code
        )
        base = next((c for c in commodities if c.id == base_currency_id), None)
        self._symbol_for = {c.code: c for c in commodities}
        dated = kind == "holdings-pie"

        # -- the controls ------------------------------------------------------
        controls = QHBoxLayout()
        self.preset = QComboBox()
        self.start = QDateEdit(_qdate(period_for("year-to-date", self.today)[0]))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd")
        self.as_of = QDateEdit(_qdate(self.today))
        self.as_of.setCalendarPopup(True)
        self.as_of.setDisplayFormat("yyyy-MM-dd")
        if dated:
            controls.addWidget(QLabel(_("As of")))
        else:
            self.preset.addItem(_("Custom period"), "")
            for key, label in PERIOD_PRESETS:
                self.preset.addItem(_(label), key)
            self.preset.setCurrentIndex(self.preset.findData("year-to-date"))
            controls.addWidget(QLabel(_("Period")))
            controls.addWidget(self.preset)
            controls.addWidget(QLabel(_("From")))
            controls.addWidget(self.start)
            controls.addWidget(QLabel(_("To")))
        controls.addWidget(self.as_of)

        self.interval = QComboBox()
        for key, label in INTERVALS:
            self.interval.addItem(_(label), key)
        if kind in ("net-worth", "period-table"):
            controls.addWidget(self.interval)

        self.target = QComboBox()
        self.target.addItem(
            _("{code} (book currency)", code=base.code) if base else _("Book currency"),
            "",
        )
        for c in currencies:
            if base is None or c.id != base.id:
                self.target.addItem(c.code, str(c.id))
        controls.addWidget(QLabel(_("Report in")))
        controls.addWidget(self.target)

        self.closed = QCheckBox(_("Include closed accounts"))
        if kind == "period-table":
            controls.addWidget(self.closed)
        self.strict = QCheckBox(_("Fail if anything cannot be valued"))
        controls.addWidget(self.strict)
        controls.addStretch()
        self.export_button = QPushButton(_("Export CSV"))
        self.export_button.clicked.connect(self.export_csv)
        controls.addWidget(self.export_button)

        # -- the body: a chart, or the table --------------------------------------
        self.chart = QChart()
        self.chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.view = QChartView(self.chart)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.body = QStackedWidget()
        self.body.addWidget(self.view)
        self.body.addWidget(self.table)
        self.body.setCurrentWidget(self.table if kind == "period-table" else self.view)

        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(controls)
        layout.addWidget(self.body, 1)
        layout.addWidget(self.summary)
        layout.addWidget(self.status)
        self.setCentralWidget(central)

        # -- wiring ----------------------------------------------------------------
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        self.start.dateChanged.connect(lambda _: self._custom_period())
        self.as_of.dateChanged.connect(lambda _: self._custom_period())
        for combo in (self.target, self.interval):
            combo.currentIndexChanged.connect(lambda _: self.refresh())
        for box in (self.closed, self.strict):
            box.toggled.connect(lambda _: self.refresh())
        if dated:
            self.refresh()
        else:
            self._preset_chosen(self.preset.currentIndex())

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
        if self.kind != "holdings-pie" and self.preset.currentData():
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
        interval: ReportInterval = self.interval.currentData() or "month"
        if self.kind != "holdings-pie" and start > (options.as_of or self.today):
            self.status.setText(_("The period ends before it starts."))
            return
        self.status.setText(_("Loading…"))
        kind, client, book_id = self.kind, self.client, self.book_id

        def work() -> Any:
            if kind in ("income-pie", "expense-pie"):
                # Every category that moved in the period, closed or not: a pie
                # has to add up to the statement's total.
                every = ReportOptions(
                    as_of=options.as_of,
                    target_id=options.target_id,
                    include_closed=True,
                    strict=options.strict,
                )
                return (
                    client.income_statement(book_id, start=start, options=every),
                    client.accounts(book_id),
                )
            if kind == "holdings-pie":
                return client.holdings(book_id, options)
            if kind == "net-worth":
                return client.net_worth(
                    book_id, start=start, interval=interval, options=options
                )
            return client.period_table(
                book_id, start=start, interval=interval, options=options
            )

        self.executor.run(work, self._loaded, self._failed)

    def _failed(self, exc: Exception) -> None:
        self.last_error = exc
        self.report = None
        self.slices = []
        self.chart.removeAllSeries()
        self.table.clear()
        self.summary.setText("")
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setText(f"<span style='color:#b91c1c'>{exc}</span>")

    def _loaded(self, result: Any) -> None:
        self.last_error = None
        if self.kind in ("income-pie", "expense-pie"):
            report, accounts = result
            self.report = report
            self._show_category_pie(report, accounts)
        elif self.kind == "holdings-pie":
            self.report = result
            self._show_holdings_pie(result)
        elif self.kind == "net-worth":
            self.report = result
            self._show_net_worth(result)
        else:
            self.report = result
            self._show_period_table(result)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setText(
            _(
                "{report} in {currency}",
                report=_(CHART_TITLES[self.kind]),
                currency=self.report.target,
            )
        )

    # -- rendering -------------------------------------------------------------------
    def _amount_text(self, minor: int, code: str) -> str:
        commodity = self._symbol_for.get(code)
        scale = commodity.minor_unit_factor if commodity else 100
        return format_amount(
            AmountOut(
                commodity_id=commodity.id if commodity else UUID(int=0),
                commodity=code,
                minor=minor,
                scale=scale,
                text=str(minor),
            )
        )

    def _draw_pie(self, slices: list[tuple[str, int]], code: str, title: str) -> None:
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
        self.slices = slices
        self.chart.setTitle(title)
        series = QPieSeries()
        total = sum(v for _, v in slices)
        for i, (label, value) in enumerate(slices):
            share = value / total if total else 0
            piece = series.append(
                f"{label}  {self._amount_text(value, code)}  ({share:.0%})", value
            )
            piece.setColor(QColor(CHART_PALETTE[i % len(CHART_PALETTE)]))
            piece.setLabelVisible(share >= 0.04)
        self.chart.addSeries(series)
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
        if not slices:
            nothing = _("nothing to show")
            self.chart.setTitle(f"{title} — {nothing}")

    def _show_category_pie(
        self, report: IncomeStatementOut, accounts: list[AccountOut]
    ) -> None:
        wanted = "INCOME" if self.kind == "income-pie" else "EXPENSE"
        classes = {a.id: a.account_class for a in accounts}
        labelled = [
            (
                row.path.rsplit(":", 1)[-1],
                row.own_value.minor * (-1 if wanted == "INCOME" else 1),
            )
            for row in report.rows
            if classes.get(row.account_id) == wanted
        ]
        slices = pie_slices(labelled, other_label=_("Other"))
        total = report.income_earned if wanted == "INCOME" else report.expenses
        self._draw_pie(slices, report.target, _(CHART_TITLES[self.kind]))
        self.summary.setText(
            _(
                "{count} categories, {total} in total",
                count=len(slices),
                total=f"<b>{self._amount_text(total.minor, report.target)} {report.target}</b>",
            )
        )

    def _show_holdings_pie(self, report: HoldingsOut) -> None:
        labelled = [
            (p.path.rsplit(":", 1)[-1], p.market_value.minor) for p in report.positions
        ]
        slices = pie_slices(labelled, other_label=_("Other"))
        self._draw_pie(slices, report.target, _(CHART_TITLES[self.kind]))
        self.summary.setText(
            _(
                "{count} positions worth {total}",
                count=len(report.positions),
                total=f"<b>{self._amount_text(report.total_value.minor, report.target)} {report.target}</b>",
            )
        )

    def _show_net_worth(self, report: NetWorthSeriesOut) -> None:
        self.chart.removeAllSeries()
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)
        self.slices = []
        self.chart.setTitle(_(CHART_TITLES[self.kind]))
        commodity = self._symbol_for.get(report.target)
        scale = commodity.minor_unit_factor if commodity else 100
        # Assets first and the net-worth line last, drawn on top, in the same
        # colours as the web app (green, vermillion, blue: Okabe-Ito).
        lines = {
            _("Assets"): ("#009e73", [p.assets.minor for p in report.points]),
            _("Liabilities"): ("#d55e00", [-p.liabilities.minor for p in report.points]),
            _("Net worth"): ("#0072b2", [p.net_worth.minor for p in report.points]),
        }
        x_axis = QDateTimeAxis()
        x_axis.setFormat("yyyy-MM-dd" if report.interval == "month" else "yyyy-MM")
        y_axis = QValueAxis()
        y_axis.setLabelFormat("%.0f")
        y_axis.setTitleText(report.target)
        self.chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        low = high = 0.0
        for name, (colour, values) in lines.items():
            series = QLineSeries()
            series.setName(name)
            pen = series.pen()
            pen.setColor(QColor(colour))
            pen.setWidth(2)
            series.setPen(pen)
            for point, value in zip(report.points, values, strict=True):
                series.append(QPointF(_msecs(point.as_of), value / scale))
                low, high = min(low, value / scale), max(high, value / scale)
            self.chart.addSeries(series)
            series.attachAxis(x_axis)
            series.attachAxis(y_axis)
        if report.points:
            x_axis.setRange(
                QDateTime.fromMSecsSinceEpoch(int(_msecs(report.points[0].as_of))),
                QDateTime.fromMSecsSinceEpoch(int(_msecs(report.points[-1].as_of))),
            )
            margin = (high - low) * 0.05 or 1.0
            y_axis.setRange(low - margin, high + margin)
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        if report.points:
            first_point, last_point = report.points[0], report.points[-1]
            change = last_point.net_worth.minor - first_point.net_worth.minor
            self.summary.setText(
                _(
                    "Net worth {first} → {last}, a change of {change}",
                    first=f"{self._amount_text(first_point.net_worth.minor, report.target)} {report.target}",
                    last=f"<b>{self._amount_text(last_point.net_worth.minor, report.target)} {report.target}</b>",
                    change=("+" if change >= 0 else "")
                    + self._amount_text(change, report.target),
                )
            )
        else:
            self.summary.setText("")

    def _show_period_table(self, report: PeriodTableOut) -> None:
        self.slices = []
        table = self.table
        table.clear()
        headers = [_("Account"), *[c.label for c in report.columns], _("Total")]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(report.rows) + 3)
        code = report.target

        def put(
            row: int, column: int, text: str, *, bold: bool = False, numeric: bool = True
        ) -> None:
            item = QTableWidgetItem(text)
            if numeric:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if bold:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            table.setItem(row, column, item)

        for r, row in enumerate(report.rows):
            name = row.path.rsplit(":", 1)[-1]
            put(r, 0, "    " * row.depth + name, bold=row.depth == 0, numeric=False)
            for c, cell in enumerate(row.display_cells, start=1):
                put(r, c, "" if cell.minor == 0 else self._amount_text(cell.minor, code))
            put(
                r,
                len(headers) - 1,
                self._amount_text(row.display_total.minor, code),
                bold=True,
            )
        base = len(report.rows)
        for offset, (label, amounts) in enumerate(
            (
                (_("Income"), report.income_earned),
                (_("Expenses"), report.expenses),
                (_("Net"), report.net),
            )
        ):
            put(base + offset, 0, label, bold=True, numeric=False)
            for c, amount in enumerate(amounts, start=1):
                put(base + offset, c, self._amount_text(amount.minor, code), bold=True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.summary.setText(
            _(
                "{columns} periods; net over the whole range {net}",
                columns=len(report.columns),
                net=f"<b>{self._amount_text(report.net[-1].minor, code)} {code}</b>",
            )
        )

    # -- export ------------------------------------------------------------------------
    def csv_text(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        report = self.report
        if report is None:
            return ""
        if self.kind in ("income-pie", "expense-pie", "holdings-pie"):
            writer.writerow(["label", "amount", "currency"])
            for label, minor in self.slices:
                writer.writerow(
                    [label, self._amount_text(minor, report.target), report.target]
                )
        elif self.kind == "net-worth":
            writer.writerow(["as_of", "assets", "liabilities", "net_worth", "currency"])
            for p in report.points:
                writer.writerow(
                    [
                        p.as_of,
                        p.assets.text,
                        self._amount_text(-p.liabilities.minor, report.target),
                        p.net_worth.text,
                        report.target,
                    ]
                )
        else:
            writer.writerow(["account", *[c.label for c in report.columns], "total"])
            for row in report.rows:
                writer.writerow(
                    [
                        row.path,
                        *[c.text for c in row.display_cells],
                        row.display_total.text,
                    ]
                )
            writer.writerow(["Income", *[a.text for a in report.income_earned]])
            writer.writerow(["Expenses", *[a.text for a in report.expenses]])
            writer.writerow(["Net", *[a.text for a in report.net]])
        return out.getvalue()

    def export_csv(self) -> None:
        text = self.csv_text()
        if not text:
            return
        path, _filter = self.choose_path(f"{self.kind}.csv")
        if path:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)

    def choose_path(self, suggested: str) -> tuple[str, str]:
        return QFileDialog.getSaveFileName(
            self, _("Export CSV"), suggested, "CSV (*.csv)"
        )


def open_chart(
    client: LedgerClient,
    book_id: UUID,
    kind: ChartKind,
    commodities: list[CommodityOut],
    base_currency_id: UUID | None,
    *,
    executor: Executor | None = None,
    parent: QWidget | None = None,
) -> ChartWindow:
    """Make, show and return a chart window; kept alive by its parent."""
    window = ChartWindow(
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

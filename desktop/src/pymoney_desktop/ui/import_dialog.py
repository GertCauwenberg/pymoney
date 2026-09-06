"""Importing a bank statement (CSV): the dialog.

The same four steps as the web app -- choose the file, confirm the columns,
review every row, see what was posted -- on one dialog with a stacked pane and
Back / Next. Each server call runs off the UI thread through the shared
:class:`~pymoney_desktop.ui.main_window.Executor`; while one is in flight the
buttons are disabled and the status line says what is happening.

The import always goes to the server (the duplicate and transfer checks need
the book the server holds), so offline this fails exactly as creating a book
does, with the same "you are offline" message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    ColumnMappingModel,
    CsvInspectOut,
    CsvPreviewOut,
    ImportDecisionIn,
    ImportProposalOut,
    PayeeOut,
    StatementImportOut,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import LedgerClient
from ..i18n import _
from .main_window import Executor, ThreadedExecutor, format_amount, natural_sign

__all__ = ["ImportStatementDialog"]

_NONE = "\u0000"
_STATUS_LABEL = {
    "new": "",
    "duplicate": _("already imported"),
    "transfer": _("transfer"),
}


def _select(box: QComboBox, value: str) -> None:
    """Pick the entry whose data (or, failing that, text) is ``value``."""
    index = box.findData(value)
    if index < 0:
        index = box.findText(value)
    if index >= 0:
        box.setCurrentIndex(index)
    elif box.isEditable():
        box.setCurrentText(value)


class ImportStatementDialog(QDialog):
    """File → columns → review → report, for one account."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        account: AccountOut,
        accounts: list[AccountOut],
        payees: list[PayeeOut],
        *,
        executor: Executor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.account = account
        self.executor = executor or ThreadedExecutor()
        self.postable = sorted(
            (a for a in accounts if not a.placeholder and a.id != account.id),
            key=lambda a: a.path,
        )
        self.payees_by_key = {p.name.strip().casefold(): p for p in payees}
        self.payee_names = sorted(p.name for p in payees)

        self.data: bytes = b""
        self.mapping: ColumnMappingModel | None = None
        self.columns: list[str] = []
        self.sample: list[dict[str, str]] = []
        self.preview: CsvPreviewOut | None = None
        self.report: StatementImportOut | None = None

        self.setWindowTitle(
            _("Import a statement into {account}").format(account=account.path)
        )
        self.setMinimumSize(820, 560)

        self.pages = QStackedWidget()
        self.map_page = _MapPage(self)
        self.review_page = _ReviewPage(self)
        self.report_page = _ReportPage()
        for page in (self.map_page, self.review_page, self.report_page):
            self.pages.addWidget(page)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.back_button = QPushButton(_("Back"))
        self.back_button.clicked.connect(self._back)
        self.next_button = QPushButton(_("Next"))
        self.next_button.clicked.connect(self._next)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.pages, 1)
        layout.addLayout(buttons)

        self._show(0)

    # -- navigation --------------------------------------------------------
    def _show(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        on_report = index == 2
        self.back_button.setVisible(index == 1)
        self.next_button.setVisible(not on_report)
        self.close_button.setText(_("Close") if on_report else _("Cancel"))
        if index == 0:
            self.next_button.setText(
                _("Review transactions") if self.mapping else _("Choose a file…")
            )
        elif index == 1:
            self._update_import_button()
        self.status.setText("")

    def _busy(self, message: str) -> None:
        self.status.setText(message)
        for button in (self.back_button, self.next_button):
            button.setEnabled(False)

    def _ready(self) -> None:
        for button in (self.back_button, self.next_button):
            button.setEnabled(True)
        self.status.setText("")

    def _fail(self, title: str) -> Callable[[Exception], None]:
        def handle(exc: Exception) -> None:
            self._ready()
            self.status.setText(f"{title}: {exc}")

        return handle

    def _back(self) -> None:
        self._show(0)

    def _next(self) -> None:
        if self.pages.currentIndex() == 0:
            if self.mapping is None:
                self._pick_file()
            else:
                self._run_preview()
        else:
            self._run_commit()

    # -- step 1: the file -------------------------------------------------
    def _pick_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Choose a statement"), "", _("CSV files (*.csv);;All files (*)")
        )
        if not path:
            return
        with open(path, "rb") as handle:
            self.data = handle.read()
        self._busy(_("Reading the file…"))
        client, book_id, account = self.client, self.book_id, self.account

        def work() -> tuple[CsvInspectOut, ColumnMappingModel | None]:
            inspect = client.inspect_csv(book_id, self.data)
            # A saved mapping is a convenience; losing it must not lose the file.
            try:
                saved = client.import_mapping(book_id, account.id)
            except Exception:
                saved = None
            return inspect, saved

        self.executor.run(work, self._inspected, self._fail(_("Could not read the file")))

    def _inspected(self, result: tuple[CsvInspectOut, ColumnMappingModel | None]) -> None:
        inspect, saved = result
        self.columns = list(inspect.columns)
        self.sample = [dict(row) for row in inspect.sample_rows]
        if saved is not None and _fits(saved, set(self.columns)):
            mapping = saved
        else:
            mapping = inspect.suggested_mapping
        self.mapping = mapping
        self.map_page.load(self.columns, self.sample, mapping)
        self._ready()
        self.next_button.setText(_("Review transactions"))

    # -- step 2: review ---------------------------------------------------
    def _run_preview(self) -> None:
        mapping = self.map_page.mapping()
        self.mapping = mapping
        self._busy(_("Checking the statement against the book…"))
        client, book_id, account = self.client, self.book_id, self.account
        self.executor.run(
            lambda: client.preview_csv(book_id, account.id, self.data, mapping),
            self._previewed,
            self._fail(_("That mapping does not fit the file")),
        )

    def _previewed(self, preview: CsvPreviewOut) -> None:
        self.preview = preview
        self.review_page.load(preview.proposals)
        self._ready()
        self._show(1)

    def _update_import_button(self) -> None:
        count = self.review_page.to_post()
        blocked = self.review_page.needs_fallback() and not self.review_page.fallback_id()
        self.next_button.setText(
            _("Import {n} transaction(s)").format(n=count)
            if count
            else _("Nothing to import")
        )
        self.next_button.setEnabled(count > 0 and not blocked)

    # -- step 3: commit --------------------------------------------------
    def _run_commit(self) -> None:
        assert self.mapping is not None
        decisions = self.review_page.decisions()
        fallback = self.review_page.fallback_id()
        self._busy(_("Importing…"))
        client, book_id, account, mapping, data = (
            self.client,
            self.book_id,
            self.account,
            self.mapping,
            self.data,
        )
        self.executor.run(
            lambda: client.commit_csv(
                book_id,
                account.id,
                data,
                mapping,
                decisions,
                uncategorised_account_id=fallback,
            ),
            self._imported,
            self._fail(_("The import was refused")),
        )

    def _imported(self, report: StatementImportOut) -> None:
        self.report = report
        self.report_page.load(report)
        self._ready()
        self._show(2)


def _fits(mapping: ColumnMappingModel, columns: set[str]) -> bool:
    used = [
        mapping.date,
        mapping.amount,
        mapping.debit,
        mapping.credit,
        mapping.reference,
        mapping.memo,
        *mapping.description,
    ]
    return all(name in columns for name in used if name)


# ==========================================================================
# page 1: the column mapping
# ==========================================================================
class _MapPage(QWidget):
    def __init__(self, dialog: ImportStatementDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.columns: list[str] = []

        self.date = QComboBox()
        self.date_format = QLineEdit()
        self.date_format.setPlaceholderText(_("auto-detect"))
        self.amount_kind = QComboBox()
        self.amount_kind.addItem(_("One signed column"), "signed")
        self.amount_kind.addItem(_("Separate out / in columns"), "debit_credit")
        self.amount_kind.currentIndexChanged.connect(self._kind_changed)
        self.amount = QComboBox()
        self.debit = QComboBox()
        self.credit = QComboBox()
        self.decimal = QComboBox()
        self.decimal.addItem(_("Full stop  (1,234.56)"), ".")
        self.decimal.addItem(_("Comma  (1.234,56)"), ",")
        self.description = QComboBox()
        self.reference = QComboBox()
        self.memo = QComboBox()
        self.invert = QCheckBox(_("Flip every sign (a withdrawal is listed positive)"))

        form = QFormLayout()
        form.addRow(_("Date column"), self.date)
        form.addRow(_("Date format"), self.date_format)
        form.addRow(_("Amount"), self.amount_kind)
        self.amount_row = self.amount
        form.addRow(_("Amount column"), self.amount)
        form.addRow(_("Money out"), self.debit)
        form.addRow(_("Money in"), self.credit)
        form.addRow(_("Decimal separator"), self.decimal)
        form.addRow(_("Description"), self.description)
        form.addRow(_("Bank reference"), self.reference)
        form.addRow(_("Extra memo"), self.memo)
        form.addRow("", self.invert)
        self._form = form
        self._amount_label = form.labelForField(self.amount)
        self._debit_label = form.labelForField(self.debit)
        self._credit_label = form.labelForField(self.credit)

        self.sample = QTableWidget(0, 0)
        self.sample.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sample.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                _(
                    "Tell PyMoney which column is which. It has guessed; adjust anything it got wrong."
                )
            )
        )
        layout.addLayout(form)
        layout.addWidget(QLabel(_("The first rows:")))
        layout.addWidget(self.sample, 1)

    def _optional(self, box: QComboBox) -> None:
        box.clear()
        box.addItem(_("— none —"), "")
        for column in self.columns:
            box.addItem(column, column)

    def _required(self, box: QComboBox) -> None:
        box.clear()
        for column in self.columns:
            box.addItem(column, column)

    def load(
        self,
        columns: list[str],
        sample: list[dict[str, str]],
        mapping: ColumnMappingModel,
    ) -> None:
        self.columns = columns
        self._required(self.date)
        self._required(self.amount)
        for box in (self.debit, self.credit, self.description, self.reference, self.memo):
            self._optional(box)
        _select(self.date, mapping.date)
        self.date_format.setText(mapping.date_format)
        _select(self.amount_kind, mapping.amount_kind)
        _select(self.amount, mapping.amount or (columns[0] if columns else ""))
        _select(self.debit, mapping.debit)
        _select(self.credit, mapping.credit)
        _select(self.decimal, mapping.decimal)
        _select(self.description, mapping.description[0] if mapping.description else "")
        _select(self.reference, mapping.reference)
        _select(self.memo, mapping.memo)
        self.invert.setChecked(mapping.invert)
        self._kind_changed()
        self._load_sample(sample)

    def _kind_changed(self) -> None:
        signed = self.amount_kind.currentData() == "signed"
        for widget in (self.amount, self._amount_label):
            widget.setVisible(signed)
        for widget in (self.debit, self.credit, self._debit_label, self._credit_label):
            widget.setVisible(not signed)

    def _load_sample(self, rows: list[dict[str, str]]) -> None:
        self.sample.setColumnCount(len(self.columns))
        self.sample.setHorizontalHeaderLabels(self.columns)
        self.sample.setRowCount(min(len(rows), 6))
        for r, row in enumerate(rows[:6]):
            for c, name in enumerate(self.columns):
                self.sample.setItem(r, c, QTableWidgetItem(row.get(name, "")))

    def mapping(self) -> ColumnMappingModel:
        base = self.dialog.mapping
        source = base.model_dump() if base is not None else {}
        signed = self.amount_kind.currentData() == "signed"
        description = self.description.currentData()
        source.update(
            date=self.date.currentData() or "",
            date_format=self.date_format.text().strip(),
            amount_kind=self.amount_kind.currentData(),
            amount=self.amount.currentData() if signed else "",
            debit="" if signed else (self.debit.currentData() or ""),
            credit="" if signed else (self.credit.currentData() or ""),
            decimal=self.decimal.currentData(),
            invert=self.invert.isChecked(),
            description=[description] if description else [],
            reference=self.reference.currentData() or "",
            memo=self.memo.currentData() or "",
        )
        return ColumnMappingModel.model_validate(source)


# ==========================================================================
# page 2: review every row
# ==========================================================================
_HEADERS = ("", _("Date"), _("Amount"), _("Description"), _("Payee"), _("Category"))


class _ReviewPage(QWidget):
    def __init__(self, dialog: ImportStatementDialog) -> None:
        super().__init__()
        self.dialog = dialog
        self.proposals: list[ImportProposalOut] = []
        self.row_widgets: list[dict[str, Any]] = []

        self.summary = QLabel("")
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self.fallback_label = QLabel(_("Put uncategorised rows in:"))
        self.fallback = QComboBox()
        self.fallback.currentIndexChanged.connect(
            lambda _i: dialog._update_import_button()
        )
        fallback_row = QHBoxLayout()
        fallback_row.addWidget(self.fallback_label)
        fallback_row.addWidget(self.fallback, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.table, 1)
        layout.addLayout(fallback_row)

    def load(self, proposals: list[ImportProposalOut]) -> None:
        self.proposals = proposals
        kinds = {
            k: sum(1 for p in proposals if p.status == k)
            for k in ("new", "duplicate", "transfer")
        }
        self.summary.setText(
            _(
                "{new} new, {duplicate} already imported, {transfer} transfers. Everything but the date and amount is editable."
            ).format(**kinds)
        )
        self.fallback.blockSignals(True)
        self.fallback.clear()
        self.fallback.addItem(_("— choose an account —"), _NONE)
        for account in self.dialog.postable:
            self.fallback.addItem(account.path, str(account.id))
        self.fallback.blockSignals(False)

        # Every row is built first; signals are wired only once the page can
        # answer "what would you import". A category combo's own
        # currentIndexChanged fires while it is being populated.
        self.table.setRowCount(len(proposals))
        self.row_widgets = []
        sign = natural_sign(self.dialog.account.account_class)
        for r, proposal in enumerate(proposals):
            post = QCheckBox()
            post.setChecked(proposal.default_action == "post")
            cell = QWidget()
            box = QHBoxLayout(cell)
            box.setContentsMargins(6, 0, 0, 0)
            box.addWidget(post)
            self.table.setCellWidget(r, 0, cell)

            date_text = proposal.date.isoformat()
            tag = _STATUS_LABEL.get(proposal.status, "")
            if proposal.status == "transfer" and proposal.existing_account_path:
                tag = _("transfer — {account}").format(
                    account=proposal.existing_account_path
                )
            date_item = QTableWidgetItem(f"{date_text}\n{tag}" if tag else date_text)
            self.table.setItem(r, 1, date_item)

            amount = QTableWidgetItem(format_amount(proposal.amount, sign=sign))
            amount.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(r, 2, amount)
            self.table.setItem(r, 3, QTableWidgetItem(proposal.description))

            payee = QComboBox()
            payee.setEditable(True)
            payee.addItem("")
            for name in self.dialog.payee_names:
                payee.addItem(name)
            payee.setCurrentText(proposal.suggested_payee_name)
            self.table.setCellWidget(r, 4, payee)

            category = QComboBox()
            category.addItem(_("— uncategorised —"), _NONE)
            for account in self.dialog.postable:
                category.addItem(account.path, str(account.id))
            if proposal.suggested_category_id is not None:
                _select(category, str(proposal.suggested_category_id))
            self.table.setCellWidget(r, 5, category)

            self.row_widgets.append({"post": post, "payee": payee, "category": category})
            self._apply_enabled(r)

        for r, widgets in enumerate(self.row_widgets):
            widgets["post"].stateChanged.connect(lambda _s, row=r: self._row_toggled(row))
            widgets["category"].currentIndexChanged.connect(self._category_changed)
        self.table.resizeRowsToContents()
        self._sync_fallback()
        self.dialog._update_import_button()

    def _category_changed(self) -> None:
        self._sync_fallback()
        self.dialog._update_import_button()

    def _row_toggled(self, row: int) -> None:
        self._apply_enabled(row)
        self._sync_fallback()
        self.dialog._update_import_button()

    def _apply_enabled(self, row: int) -> None:
        widgets = self.row_widgets[row]
        on = widgets["post"].isChecked()
        widgets["payee"].setEnabled(on)
        widgets["category"].setEnabled(on)

    def _sync_fallback(self) -> None:
        show = self.needs_fallback()
        self.fallback_label.setVisible(show)
        self.fallback.setVisible(show)

    # -- what the dialog asks it ------------------------------------------
    def to_post(self) -> int:
        return sum(1 for w in self.row_widgets if w["post"].isChecked())

    def needs_fallback(self) -> bool:
        return any(
            w["post"].isChecked() and w["category"].currentData() == _NONE
            for w in self.row_widgets
        )

    def fallback_id(self) -> UUID | None:
        data = self.fallback.currentData()
        return None if not data or data == _NONE else UUID(data)

    def decisions(self) -> list[ImportDecisionIn]:
        out: list[ImportDecisionIn] = []
        for proposal, widgets in zip(self.proposals, self.row_widgets, strict=True):
            if not widgets["post"].isChecked():
                out.append(ImportDecisionIn(row_key=proposal.row_key, action="skip"))
                continue
            name = widgets["payee"].currentText().strip()
            existing = self.dialog.payees_by_key.get(name.casefold())
            category = widgets["category"].currentData()
            out.append(
                ImportDecisionIn(
                    row_key=proposal.row_key,
                    action="post",
                    payee_id=existing.id if existing else None,
                    new_payee="" if existing or not name else name,
                    category_id=None if category == _NONE else UUID(category),
                )
            )
        return out


# ==========================================================================
# page 3: the report
# ==========================================================================
class _ReportPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.heading = QLabel("")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.summary)
        layout.addWidget(self.detail, 1)

    def load(self, report: StatementImportOut) -> None:
        self.heading.setText(_("Imported") if report.ok else _("Imported, with errors"))
        self.summary.setText(report.summary)
        lines: list[str] = []
        for problem in report.problems:
            lines.append(f"[{problem.level}] {problem.message}")
        self.detail.setText("\n".join(lines))

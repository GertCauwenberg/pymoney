"""Scheduled / recurring transactions on the desktop: the due dialog and a
small manager.

Nothing posts on a timer. :class:`DueDialog` lists what the server (or the
replica) reports due and posts each occurrence on the user's click, through
the same idempotent endpoint the web app uses.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    DueScheduleOut,
    PostOccurrenceIn,
    RecurrenceModel,
    ScheduleIn,
    ScheduleTemplateIn,
    SkipOccurrenceIn,
    TemplateLegIn,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..client import LedgerClient
from ..i18n import _
from .ui_utils import Executor, ThreadedExecutor, format_amount

__all__ = ["DueDialog", "ScheduleForm", "SchedulesDialog"]

_UNITS = [
    ("DAILY", "days"),
    ("WEEKLY", "weeks"),
    ("MONTHLY", "months"),
    ("YEARLY", "years"),
]


class DueDialog(QDialog):
    """What is due, with Enter / Skip per occurrence."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        rows: list[DueScheduleOut],
        *,
        executor: Executor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.executor = executor or ThreadedExecutor()
        self.rows = list(rows)
        self.posted = 0
        self.setWindowTitle(_("Scheduled transactions due"))
        self.setMinimumWidth(560)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.status = QLabel("")
        self.enter_button = QPushButton(_("Enter"))
        self.skip_button = QPushButton(_("Skip"))
        self.enter_button.clicked.connect(lambda: self._act(post=True))
        self.skip_button.clicked.connect(lambda: self._act(post=False))
        close = QPushButton(_("Close"))
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.enter_button)
        buttons.addWidget(self.skip_button)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(_("Nothing has been posted. Select one and enter it, or skip it."))
        )
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for row in self.rows:
            s = row.schedule
            amount = (
                format_amount(row.amount) if row.amount is not None else _("estimate")
            )
            extra = f"  (+{len(row.pending) - 1})" if len(row.pending) > 1 else ""
            item = QListWidgetItem(f"{row.pending[0]}   {s.name}   {amount}{extra}")
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.enter_button.setEnabled(bool(self.rows))
        self.skip_button.setEnabled(bool(self.rows))

    def _selected(self) -> tuple[UUID, date] | None:
        if self.list.currentRow() < 0:
            return None
        sid = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        for row in self.rows:
            if row.schedule.id == sid:
                return sid, row.pending[0]
        return None

    def _act(self, *, post: bool) -> None:
        picked = self._selected()
        if picked is None:
            return
        sid, on = picked
        self.status.setText(_("Working…"))
        self.enter_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        client, book_id = self.client, self.book_id

        def work() -> None:
            if post:
                client.post_occurrence(book_id, sid, PostOccurrenceIn(on=on))
            else:
                client.skip_occurrence(book_id, sid, SkipOccurrenceIn(on=on))

        def done(_r: object) -> None:
            if post:
                self.posted += 1
            self.rows = self.client.due_schedules(book_id).due
            self.status.setText("")
            self._fill()
            if not self.rows:
                self.accept()

        def failed(exc: Exception) -> None:
            self.status.setText(f"{exc}")
            self.enter_button.setEnabled(True)
            self.skip_button.setEnabled(True)

        self.executor.run(work, done, failed)


class ScheduleForm(QDialog):
    """A two-sided template and a recurrence rule. Covers rent/salary/subs."""

    def __init__(
        self,
        accounts: list[AccountOut],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("New schedule"))
        self.accounts = [a for a in accounts if not a.placeholder]
        self.name = QLineEdit()
        self.incoming = QCheckBox(_("Money coming in (a salary)"))
        self.whose = QComboBox()
        self.other = QComboBox()
        for a in self.accounts:
            self.whose.addItem(a.path, str(a.id))
            self.other.addItem(a.path, str(a.id))
        if self.other.count() > 1:
            self.other.setCurrentIndex(1)
        self.amount = QLineEdit()
        self.memo = QLineEdit()
        self.estimate = QCheckBox(_("The amount is an estimate"))
        self.auto = QCheckBox(_("Post without asking"))
        self.every = QSpinBox()
        self.every.setRange(1, 365)
        self.unit = QComboBox()
        for code, label in _UNITS:
            self.unit.addItem(_(label), code)
        self.unit.setCurrentIndex(2)
        self.start = QDateEdit()
        self.start.setCalendarPopup(True)
        self.start.setDate(self.start.date().currentDate())
        self.by_weekday = QCheckBox(_("On the same weekday rather than the date"))
        self.count = QSpinBox()
        self.count.setRange(0, 9999)
        self.count.setSpecialValueText(_("no end"))

        form = QFormLayout()
        form.addRow(_("Name"), self.name)
        form.addRow("", self.incoming)
        form.addRow(_("Out of / into"), self.whose)
        form.addRow(_("Amount"), self.amount)
        form.addRow(_("Category"), self.other)
        form.addRow(_("Memo"), self.memo)
        form.addRow("", self.estimate)
        every_row = QHBoxLayout()
        every_row.addWidget(self.every)
        every_row.addWidget(self.unit)
        box = QWidget()
        box.setLayout(every_row)
        form.addRow(_("Every"), box)
        form.addRow(_("Starting"), self.start)
        form.addRow("", self.by_weekday)
        form.addRow(_("End after N times"), self.count)
        form.addRow("", self.auto)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if self.name.text().strip() and self.amount.text().strip():
            self.accept()

    def to_body(self) -> ScheduleIn:
        raw = self.amount.text().strip().lstrip("-")
        signed = raw if self.incoming.isChecked() else f"-{raw}"
        frequency = self.unit.currentData()
        return ScheduleIn(
            name=self.name.text().strip(),
            auto_post=self.auto.isChecked(),
            recurrence=RecurrenceModel(
                frequency=frequency,
                start=self.start.date().toPython(),  # type: ignore[arg-type]
                interval=self.every.value(),
                monthly_by_weekday=(
                    frequency == "MONTHLY" and self.by_weekday.isChecked()
                ),
                count=self.count.value() or None,
            ),
            template=ScheduleTemplateIn(
                legs=[
                    TemplateLegIn(
                        account_id=UUID(self.whose.currentData()), value=signed
                    ),
                    TemplateLegIn(account_id=UUID(self.other.currentData())),
                ],
                memo=self.memo.text().strip(),
                estimate=self.estimate.isChecked(),
            ),
        )


class SchedulesDialog(QDialog):
    """List the book's schedules; add one."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        accounts: list[AccountOut],
        *,
        run_dialog: Callable[[QDialog], int],
        executor: Executor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.accounts = accounts
        self.run_dialog = run_dialog
        self.executor = executor or ThreadedExecutor()
        self.setWindowTitle(_("Scheduled transactions"))
        self.setMinimumWidth(560)

        self.list = QListWidget()
        add = QPushButton(_("New schedule"))
        add.clicked.connect(self._add)
        close = QPushButton(_("Close"))
        close.clicked.connect(self.accept)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(add)
        bar.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(bar)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for s in self.client.schedules(self.book_id):
            mark = " · paused" if s.paused else " · finished" if s.finished else ""
            self.list.addItem(
                f"{s.name}  —  {s.recurrence_text}  (next {s.next_due or '—'}){mark}"
            )

    def _add(self) -> None:
        form = ScheduleForm(self.accounts, parent=self)
        if self.run_dialog(form) != QDialog.DialogCode.Accepted:
            return
        body = form.to_body()
        self.executor.run(
            lambda: self.client.create_schedule(self.book_id, body),
            lambda _r: self.refresh(),
            lambda exc: self.list.addItem(_("Could not save: {e}").format(e=exc)),
        )

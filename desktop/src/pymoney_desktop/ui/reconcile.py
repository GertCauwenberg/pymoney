"""Reconciling against a statement: the dialog.

The same flow as the web app's register: a statement date, a state to mark
with, and the account's entries with a tick box each -- cleared ones pre-ticked
(the bank has seen them), frozen ones not offered (they are beyond changing),
a running count and sum of what is ticked -- and one request marks them all.
FROZEN is offered to owners only; it cannot be undone, and the server refuses
it from anyone else anyway.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pymoney.api.schemas import AccountOut, ReconcileStateName, RegisterRowOut
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _
from .main_window import format_amount, natural_sign
from .theme import amount_brush

__all__ = ["ReconcileDialog"]


class ReconcileDialog(QDialog):
    """Tick what is on the statement; the caller sends it as one command."""

    def __init__(
        self,
        account: AccountOut,
        rows: list[RegisterRowOut],
        payees: dict[UUID, str],
        *,
        can_freeze: bool = False,
        today: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.account = account
        self.sign = natural_sign(account.account_class)
        self.setWindowTitle(_("Reconcile against a statement"))
        self.setMinimumWidth(720)
        self.resize(820, 560)
        today = today or date.today()
        # Only this account's own splits go on its statement; frozen ones are
        # already beyond changing.
        self.rows = [
            r
            for r in rows
            if r.account_id == account.id and r.reconcile_state.value != "FROZEN"
        ]

        intro = QLabel(
            _(
                "Tick every entry that is on the statement, then mark them all at once. Cleared entries are pre-ticked."
            )
        )
        intro.setWordWrap(True)

        form = QFormLayout()
        self.date = QDateEdit(QDate(today.year, today.month, today.day))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        form.addRow(_("Statement date"), self.date)
        self.state_box = QComboBox()
        self.state_box.addItem(_("Cleared"), "CLEARED")
        self.state_box.addItem(_("Reconciled"), "RECONCILED")
        if can_freeze:
            self.state_box.addItem(_("Frozen"), "FROZEN")
        self.state_box.setCurrentIndex(1)
        form.addRow(_("Mark as"), self.state_box)

        self.warning = QLabel(
            _("Frozen is final: these entries can never be edited or deleted again.")
        )
        self.warning.setStyleSheet("color: #bc4700")
        self.warning.setVisible(False)
        self.state_box.currentIndexChanged.connect(
            lambda _i: self.warning.setVisible(self.state_box.currentData() == "FROZEN")
        )

        self.table = QTableWidget(len(self.rows), 5)
        self.table.setHorizontalHeaderLabels(
            [_("On the statement"), _("Date"), _("Payee"), _("Memo"), _("Amount")]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        for i, row in enumerate(self.rows):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(
                Qt.CheckState.Checked
                if row.reconcile_state.value in ("CLEARED", "RECONCILED")
                else Qt.CheckState.Unchecked
            )
            self.table.setItem(i, 0, tick)
            self.table.setItem(i, 1, QTableWidgetItem(row.date.isoformat()))
            self.table.setItem(
                i,
                2,
                QTableWidgetItem(payees.get(row.payee_id, "") if row.payee_id else ""),
            )
            self.table.setItem(i, 3, QTableWidgetItem(row.memo))
            amount = QTableWidgetItem(format_amount(row.quantity, sign=self.sign))
            amount.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            brush = amount_brush(row.quantity.minor * self.sign)
            if brush is not None:
                amount.setForeground(brush)
            self.table.setItem(i, 4, amount)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemChanged.connect(lambda _item: self._refresh())

        picks = QHBoxLayout()
        self.all_button = QPushButton(_("Tick all"))
        self.all_button.clicked.connect(lambda: self._tick_all(True))
        self.none_button = QPushButton(_("Untick all"))
        self.none_button.clicked.connect(lambda: self._tick_all(False))
        picks.addWidget(self.all_button)
        picks.addWidget(self.none_button)
        picks.addStretch()
        self.summary = QLabel()
        picks.addWidget(self.summary)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Mark them"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.warning)
        layout.addWidget(self.table, 1)
        layout.addLayout(picks)
        layout.addWidget(self.buttons)
        self._refresh()

    # -- what was chosen ----------------------------------------------------------
    def selected_ids(self) -> list[UUID]:
        out = []
        for i, row in enumerate(self.rows):
            item = self.table.item(i, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                out.append(row.split_id)
        return out

    def state(self) -> ReconcileStateName:
        return ReconcileStateName(self.state_box.currentData())

    def statement_date(self) -> date:
        q = self.date.date()
        return date(q.year(), q.month(), q.day())

    # -- helpers ----------------------------------------------------------------------
    def _tick_all(self, on: bool) -> None:
        self.table.blockSignals(True)
        for i in range(len(self.rows)):
            item = self.table.item(i, 0)
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
                )
        self.table.blockSignals(False)
        self._refresh()

    def _refresh(self) -> None:
        chosen = set(self.selected_ids())
        picked = [r for r in self.rows if r.split_id in chosen]
        total = sum(r.quantity.minor for r in picked)
        if picked:
            amount = picked[0].quantity.model_copy(update={"minor": total})
            text = f"{format_amount(amount, sign=self.sign)} {amount.commodity}"
        else:
            text = "—"
        self.summary.setText(_("{n} selected, totalling", n=len(picked)) + f" {text}")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(picked))

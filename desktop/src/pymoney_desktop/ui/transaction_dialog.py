"""Post or edit a transaction."""

from __future__ import annotations

from uuid import UUID

from pymoney.api.schemas import AccountOut, CommodityOut, PayeeOut, TransactionOut
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..drafts import (
    LegDraft,
    TransactionDraft,
    balance,
    draft_from_transaction,
    minor_to_text,
    problems,
)
from ..i18n import _

__all__ = ["TransactionDialog"]

NEW_PAYEE = "__new__"


class TransactionDialog(QDialog):
    """Date, payee (or a new one), memo, number, and a table of legs.

    Leave one amount blank and it balances the entry; the footer shows what it
    will receive. Amounts are sent as typed; see :mod:`pymoney_desktop.drafts`.
    """

    def __init__(
        self,
        accounts: list[AccountOut],
        payees: list[PayeeOut],
        currency: CommodityOut,
        *,
        existing: TransactionOut | None = None,
        default_account_id: UUID | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Edit transaction") if existing else _("New transaction"))
        self.setMinimumWidth(720)
        self._accounts = {a.id: a for a in accounts}
        self._postable = sorted(
            (a for a in accounts if not a.placeholder and not a.closed),
            key=lambda a: a.path,
        )
        self._payees = sorted(payees, key=lambda p: p.name.lower())
        self._currency = currency
        self.existing = existing
        draft = (
            draft_from_transaction(existing)
            if existing
            else TransactionDraft(
                date=QDate.currentDate().toString("yyyy-MM-dd"),
                legs=[LegDraft(account_id=default_account_id), LegDraft()],
            )
        )
        self._keep = [leg.keep for leg in draft.legs]

        form = QFormLayout()
        self.date = QDateEdit(QDate.fromString(draft.date, "yyyy-MM-dd"))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        form.addRow(_("Date"), self.date)
        self.payee = QComboBox()
        self.payee.addItem("—", None)
        for p in self._payees:
            # Ids travel as strings: QComboBox.findData compares QVariants, and
            # a Python UUID object does not match itself through one.
            self.payee.addItem(p.name, str(p.id))
        self.payee.addItem(_("New payee…"), NEW_PAYEE)
        if draft.payee_id is not None:
            index = self.payee.findData(str(draft.payee_id))
            if index >= 0:
                self.payee.setCurrentIndex(index)
        form.addRow(_("Payee"), self.payee)
        self.new_payee = QLineEdit()
        self.new_payee.setPlaceholderText(_("Name of the new payee"))
        self.new_payee.setVisible(False)
        form.addRow("", self.new_payee)
        self.payee.currentIndexChanged.connect(
            lambda _: self.new_payee.setVisible(self.payee.currentData() == NEW_PAYEE)
        )
        self.memo = QLineEdit(draft.memo)
        form.addRow(_("Memo"), self.memo)
        self.number = QLineEdit(draft.number)
        self.number.setPlaceholderText(_("Cheque or reference"))
        form.addRow(_("Number"), self.number)

        self.legs = QTableWidget(0, 4)
        self.legs.setHorizontalHeaderLabels(
            [_("Account"), _("Leg memo"), _("Quantity"), f"Amount ({currency.code})"]
        )
        self.legs.horizontalHeader().setStretchLastSection(False)
        self.legs.setColumnWidth(0, 280)
        self.legs.setColumnWidth(1, 160)
        for leg in draft.legs:
            self._add_leg(leg)

        buttons = QHBoxLayout()
        add = QPushButton(_("Add leg"))
        add.clicked.connect(lambda: self._add_leg(LegDraft()))
        remove = QPushButton(_("Remove leg"))
        remove.clicked.connect(self._remove_leg)
        self.footer = QLabel()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        buttons.addWidget(self.footer)

        hint = QLabel(
            _(
                _(
                    "Money into an account is positive, out of it negative. Leave one amount blank and it takes whatever balances the entry."
                )
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            _("Save") if existing else _("Post")
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.legs)
        layout.addLayout(buttons)
        layout.addWidget(hint)
        layout.addWidget(self.buttons)
        self.legs.cellChanged.connect(lambda *_: self._refresh_footer())
        self._refresh_footer()

    # -- legs ------------------------------------------------------------------
    def _add_leg(self, leg: LegDraft) -> None:
        row = self.legs.rowCount()
        self.legs.insertRow(row)
        combo = QComboBox()
        combo.addItem(_("Choose an account…"), None)
        if (
            leg.account_id in self._accounts
            and self._accounts[leg.account_id] not in self._postable
        ):
            stale = self._accounts[leg.account_id]
            combo.addItem(stale.path, str(stale.id))
        for a in self._postable:
            label = a.path + (
                f" ({a.commodity})" if a.commodity_id != self._currency.id else ""
            )
            combo.addItem(label, str(a.id))
        if leg.account_id is not None:
            combo.setCurrentIndex(max(0, combo.findData(str(leg.account_id))))
        combo.currentIndexChanged.connect(lambda _: self._refresh_footer())
        self.legs.setCellWidget(row, 0, combo)
        self.legs.setItem(row, 1, QTableWidgetItem(leg.memo))
        self.legs.setItem(row, 2, QTableWidgetItem(leg.quantity))
        amount = QTableWidgetItem(leg.value)
        amount.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.legs.setItem(row, 3, amount)
        if len(self._keep) <= row:
            self._keep.append({})

    def _remove_leg(self) -> None:
        row = self.legs.currentRow()
        if row >= 0 and self.legs.rowCount() > 2:
            self.legs.removeRow(row)
            del self._keep[row]
            self._refresh_footer()

    def _cell(self, row: int, col: int) -> str:
        item = self.legs.item(row, col)
        return item.text() if item is not None else ""

    def draft(self) -> TransactionDraft:
        legs = []
        for row in range(self.legs.rowCount()):
            combo = self.legs.cellWidget(row, 0)
            chosen = combo.currentData() if isinstance(combo, QComboBox) else None
            account_id = UUID(chosen) if chosen else None
            legs.append(
                LegDraft(
                    account_id=account_id,
                    memo=self._cell(row, 1),
                    quantity=self._cell(row, 2),
                    value=self._cell(row, 3),
                    keep=self._keep[row],
                )
            )
        payee = self.payee.currentData()
        return TransactionDraft(
            date=self.date.date().toString("yyyy-MM-dd"),
            payee_id=None if payee in (None, NEW_PAYEE) else UUID(payee),
            new_payee=self.new_payee.text().strip() if payee == NEW_PAYEE else "",
            memo=self.memo.text(),
            number=self.number.text(),
            legs=legs,
        )

    def _refresh_footer(self) -> None:
        total, blank = balance(self.draft(), self._currency)
        factor = self._currency.minor_unit_factor
        if blank:
            self.footer.setText(
                _(
                    "blank leg gets {amount}",
                    amount=f"{minor_to_text(-total, factor)} {self._currency.code}",
                )
            )
        elif total == 0:
            self.footer.setText(_("balanced"))
        else:
            self.footer.setText(
                _(
                    "off by {amount}",
                    amount=f"{minor_to_text(total, factor)} {self._currency.code}",
                )
            )

    def _accept(self) -> None:
        draft = self.draft()
        found = problems(draft, self._accounts, self._currency)
        if self.payee.currentData() == NEW_PAYEE and not draft.new_payee:
            found.append(_("Give the new payee a name."))
        if found:
            QMessageBox.warning(self, _("Not yet"), "\n".join(found))
            return
        self.accept()

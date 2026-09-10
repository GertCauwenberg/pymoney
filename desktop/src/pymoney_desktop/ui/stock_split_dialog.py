"""Record a share split on a holding.

A date, a ratio and an optional memo. The ratio is new-for-old -- ``2`` for a
two-for-one split, ``1/2`` for a one-for-two reverse split -- and accepts both
a whole number and a ``num/den`` form, the same as a price. The server does
the arithmetic: it books the change in share count and, where it has a price
on file, a split-adjusted one.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

from pymoney.api.schemas import AccountOut, StockSplitIn
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ..i18n import _

__all__ = ["StockSplitDialog"]


class StockSplitDialog(QDialog):
    def __init__(self, account: AccountOut, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.account = account
        self.setWindowTitle(_("Stock split"))
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.addRow(
            QLabel(
                _("{account} is held in {commodity}.").format(
                    account=account.path, commodity=account.commodity
                )
            )
        )
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        form.addRow(_("Date"), self.date)
        self.ratio = QLineEdit()
        self.ratio.setPlaceholderText(_('new-for-old, e.g. "2" or "1/3"'))
        form.addRow(_("Ratio"), self.ratio)
        self.memo = QLineEdit()
        form.addRow(_("Memo"), self.memo)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Record"))
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

    def _accept(self) -> None:
        if self._ratio() is None:
            self.ratio.setFocus()
            return
        self.accept()

    def _ratio(self) -> Fraction | None:
        text = self.ratio.text().strip()
        try:
            value = Fraction(text)
        except (ValueError, ZeroDivisionError):
            return None
        return value if value > 0 and value != 1 else None

    def body(self) -> StockSplitIn:
        ratio = self._ratio()
        assert ratio is not None  # _accept refuses to close otherwise
        q = self.date.date()
        return StockSplitIn(
            date=date(q.year(), q.month(), q.day()),
            ratio=f"{ratio.numerator}/{ratio.denominator}",
            memo=self.memo.text().strip(),
        )

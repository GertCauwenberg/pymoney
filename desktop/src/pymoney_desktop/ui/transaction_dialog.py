"""Post or edit a transaction."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import UUID

from pymoney.api.schemas import AccountOut, CommodityOut, PayeeOut, TagOut, TransactionOut
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from ..client import LedgerClient
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

_ATTACHMENT_TYPES = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/tiff",
    "application/pdf",
)
_ATTACHMENT_FILTER = "Images and PDF (*.png *.jpg *.jpeg *.gif *.webp *.tif *.tiff *.pdf)"

# Columns of the legs table. Tags go last so the indices the amount, memo and
# account sit at do not move.
ACCOUNT, MEMO, QUANTITY, AMOUNT, TAGS = range(5)
_TAG_SEPARATORS = ",;"


def _split_tag_names(text: str) -> list[str]:
    """``"Holiday; work ,, Holiday"`` -> ``["Holiday", "work"]`` (order kept,
    case-folded duplicates dropped)."""
    out: list[str] = []
    seen: set[str] = set()
    working = text
    for sep in _TAG_SEPARATORS[1:]:
        working = working.replace(sep, _TAG_SEPARATORS[0])
    for chunk in working.split(_TAG_SEPARATORS[0]):
        name = chunk.strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


class TransactionDialog(QDialog):
    """Date, payee (or a new one), memo, number, and a table of legs.

    Leave one amount blank and it balances the entry; the footer shows what it
    will receive. Amounts are sent as typed; see :mod:`pymoney_desktop.drafts`.
    Each leg carries its own tags: type names, separated by commas; one the
    book does not have yet is created when the transaction is saved.
    """

    def __init__(
        self,
        accounts: list[AccountOut],
        payees: list[PayeeOut],
        currency: CommodityOut,
        *,
        commodities: list[CommodityOut] | None = None,
        tags: list[TagOut] | None = None,
        existing: TransactionOut | None = None,
        client: LedgerClient | None = None,
        book_id: UUID | None = None,
        default_account_id: UUID | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Edit transaction") if existing else _("New transaction"))
        self.setMinimumWidth(820)
        self._accounts = {a.id: a for a in accounts}
        self._postable = sorted(
            (a for a in accounts if not a.placeholder and not a.closed),
            key=lambda a: a.path,
        )
        self._client = client
        self._book_id = book_id
        # (filename, bytes, content-type) picked while posting; uploaded by the
        # caller once the transaction has an id. Empty when editing -- an
        # existing transaction opens its attachments straight away instead.
        self._staged: list[tuple[str, bytes, str]] = []

        self._payees = sorted(payees, key=lambda p: p.name.lower())
        self._tags = sorted(tags or [], key=lambda t: t.name.lower())
        self._tag_name_by_id = {t.id: t.name for t in self._tags}
        self._tag_id_by_key = {t.name.casefold(): t.id for t in self._tags}
        self._base_currency = currency
        self._currency = currency
        self._currencies = sorted(
            (c for c in (commodities or [currency]) if c.kind == "CURRENCY"),
            key=lambda c: c.code,
        )
        if all(c.id != currency.id for c in self._currencies):
            self._currencies.insert(0, currency)
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
        self.payee = QLineEdit()
        self.payee.setPlaceholderText(_("Choose a payee"))
        if self._payees:
            completer = QCompleter([p.name for p in self._payees], self.payee)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setModelSorting(
                QCompleter.ModelSorting.CaseInsensitivelySortedModel
            )
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            self.payee.setCompleter(completer)
        if draft.payee_id is not None:
            payee = next((p for p in self._payees if p.id == draft.payee_id), None)
            if payee:
                self.payee.insert(payee.name)
        form.addRow(_("Payee"), self.payee)
        if existing is None:
            self.payee.editingFinished.connect(lambda: self._prefill_from_payee())
        self.memo = QLineEdit(draft.memo)
        form.addRow(_("Memo"), self.memo)
        self.number = QLineEdit(draft.number)
        self.number.setPlaceholderText(_("Cheque or reference"))
        form.addRow(_("Number"), self.number)

        # The transaction currency is chosen when posting and fixed once the
        # transaction exists: its splits' values are denominated in it.
        self.currency_box = QComboBox()
        for c in self._currencies:
            label = (
                _("{code} (book currency)", code=c.code)
                if c.id == self._base_currency.id
                else c.code
            )
            self.currency_box.addItem(label, str(c.id))
        self.currency_box.setCurrentIndex(
            max(0, self.currency_box.findData(str(currency.id)))
        )
        self.currency_box.setDisabled(existing is not None)
        self.currency_box.setToolTip(_("Fixed once the transaction is posted."))
        self.currency_box.currentIndexChanged.connect(lambda _: self._currency_changed())
        form.addRow(_("Currency"), self.currency_box)

        # Attachments. An existing transaction has an id, so its receipts open
        # in a dialog of their own; a new one has none yet
        self._attachments_row = QWidget()
        attach_line = QHBoxLayout(self._attachments_row)
        attach_line.setContentsMargins(0, 0, 0, 0)
        if existing is not None:
            self.attachments_button = QPushButton(_("Attachments…"))
            self.attachments_button.clicked.connect(self._open_attachments)
            self.attachments_button.setEnabled(
                self._client is not None and self._book_id is not None
            )
            attach_line.addWidget(self.attachments_button)
        else:
            self.attachments_button = QPushButton(_("Add receipts…"))
            self.attachments_button.clicked.connect(self._pick_receipts)
            attach_line.addWidget(self.attachments_button)

        self.attachments_label = QLabel(_("None"))
        self.attachments_label.setStyleSheet("color: gray")
        attach_line.addWidget(self.attachments_label)
        attach_line.addStretch()
        form.addRow(_("Attachments"), self._attachments_row)

        self.legs = QTableWidget(0, 5)
        self.legs.cellClicked.connect(self._enable_remove_button)
        self.legs.setHorizontalHeaderLabels(
            [
                _("Account"),
                _("Leg memo"),
                _("Quantity"),
                f"Amount ({currency.code})",
                _("Tags"),
            ]
        )
        self.legs.horizontalHeader().setStretchLastSection(True)
        self.legs.setColumnWidth(ACCOUNT, 250)
        self.legs.setColumnWidth(MEMO, 130)
        self.legs.setColumnWidth(AMOUNT, 120)
        for leg in draft.legs:
            self._add_leg(leg)

        buttons = QHBoxLayout()
        add = QPushButton(_("Add leg"))
        add.clicked.connect(lambda: self._add_leg(LegDraft()))
        self.remove = QPushButton(_("Remove leg"))
        self.remove.setDisabled(True)
        self.remove.clicked.connect(self._remove_leg)
        self.footer = QLabel()
        buttons.addWidget(add)
        buttons.addWidget(self.remove)
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
        if len(self._keep) <= row:
            self._keep.append({})
        self.legs.insertRow(row)
        combo = QLineEdit()
        combo.setPlaceholderText(_("Choose an account…"))
        completer = QCompleter([a.path for a in self._postable], combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setModelSorting(QCompleter.ModelSorting.CaseInsensitivelySortedModel)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)
        if leg.account_id is not None:
            combo.insert(self._accounts[leg.account_id].path)
        self.legs.setCellWidget(row, ACCOUNT, combo)
        self.legs.setItem(row, MEMO, QTableWidgetItem(leg.memo))

        known = [
            self._tag_name_by_id[i] for i in leg.tag_ids if i in self._tag_name_by_id
        ]
        tags = QLineEdit(", ".join(known + list(leg.new_tag_names)))
        tags.setPlaceholderText(_("comma-separated"))
        if self._tags:
            completer = QCompleter([t.name for t in self._tags], tags)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            tags.setCompleter(completer)
        self.legs.setCellWidget(row, TAGS, tags)

        self.legs.setItem(row, QUANTITY, QTableWidgetItem(leg.quantity))
        amount = QTableWidgetItem(leg.value)
        amount.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.legs.setItem(row, AMOUNT, amount)

    def _remove_leg(self) -> None:
        row = self.legs.currentRow()
        if row >= 0 and self.legs.rowCount() > 2:
            self.legs.removeRow(row)
            del self._keep[row]
            self.remove.setDisabled(True)
            self._refresh_footer()

    def _enable_remove_button(self) -> None:
        if self.legs.rowCount() > 2:
            self.remove.setDisabled(False)

    def _prefill_from_payee(self) -> None:
        """The payee's default account goes into the first leg that has none --
        only into a blank one, so nothing the user chose is overwritten."""
        payeeName = self.payee.displayText()
        payee = next((p for p in self._payees if p.name == payeeName), None)
        if payee is None or payee.default_account_id is None:
            return
        for row in range(1, self.legs.rowCount()):
            combo = self.legs.cellWidget(row, ACCOUNT)
            if isinstance(combo, QLineEdit) and not combo.text():
                combo.setText(self._accounts[payee.default_account_id].path)
                return

    def _cell(self, row: int, col: int) -> str:
        item = self.legs.item(row, col)
        return item.text() if item is not None else ""

    def _tags_text(self, row: int) -> str:
        widget = self.legs.cellWidget(row, TAGS)
        return widget.text() if isinstance(widget, QLineEdit) else ""

    # -- attachments -------------------------------------------------------
    def staged_attachments(self) -> list[tuple[str, bytes, str]]:
        """Receipts the user picked while posting, as ``(name, bytes, type)``;
        the caller uploads them once the transaction has an id."""
        return list(self._staged)

    def _refresh_attachment_label(self) -> None:
        names = ", ".join(name for name, _data, _type in self._staged)
        self.attachments_label.setText(names or _("None"))

    def _pick_receipts(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, _("Choose a receipt or invoice"), "", _(_ATTACHMENT_FILTER)
        )
        for path in paths:
            content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if content_type not in _ATTACHMENT_TYPES:
                QMessageBox.warning(
                    self,
                    _("Not allowed"),
                    _("Attach an image (JPEG, PNG, GIF, WebP, TIFF) or a PDF."),
                )
                continue
            self._staged.append((Path(path).name, Path(path).read_bytes(), content_type))
        self._refresh_attachment_label()

    def _open_attachments(self) -> QDialog | None:
        """The receipts for the transaction being edited. Only editors ever
        reach this dialog (the register's Edit button is theirs), so adding
        and removing is allowed; the server checks the grant regardless."""
        if self._client is None or self._book_id is None or self.existing is None:
            return None
        from .attachments_dialog import AttachmentsDialog

        self._attachments_dialog = AttachmentsDialog(
            self._client,
            self._book_id,
            self.existing.id,
            can_write=True,
            parent=self,
        )
        self._attachments_dialog.exec()
        return self._attachments_dialog

    def currency(self) -> CommodityOut:
        """The transaction currency: the user's choice for a new entry, the
        stored one for an edit (where the control is disabled)."""
        return self._currency

    def _currency_changed(self) -> None:
        chosen = self.currency_box.currentData()
        for candidate in self._currencies:
            if str(candidate.id) == chosen:
                self._currency = candidate
                break
        header = self.legs.horizontalHeaderItem(AMOUNT)
        if header is not None:
            header.setText(f"Amount ({self._currency.code})")
        self._refresh_footer()

    def draft(self) -> TransactionDraft:
        legs = []
        for row in range(self.legs.rowCount()):
            combo = self.legs.cellWidget(row, ACCOUNT)
            chosen = combo.displayText() if isinstance(combo, QLineEdit) else None
            account_id = (
                next((a.id for a in self._accounts.values() if a.path == chosen), None)
                if chosen
                else None
            )
            tag_ids: list[UUID] = []
            new_tag_names: list[str] = []
            for name in _split_tag_names(self._tags_text(row)):
                existing = self._tag_id_by_key.get(name.casefold())
                if existing is not None:
                    tag_ids.append(existing)
                else:
                    new_tag_names.append(name)
            legs.append(
                LegDraft(
                    account_id=account_id,
                    memo=self._cell(row, MEMO),
                    quantity=self._cell(row, QUANTITY),
                    value=self._cell(row, AMOUNT),
                    tag_ids=tag_ids,
                    new_tag_names=new_tag_names,
                    keep=self._keep[row],
                )
            )
        payeeName = self.payee.displayText()
        payee = next((p for p in self._payees if p.name == payeeName), None)
        return TransactionDraft(
            date=self.date.date().toString("yyyy-MM-dd"),
            payee_id=payee.id if payee else None,
            new_payee=payeeName if payee is None else "",
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
        if found:
            QMessageBox.warning(self, _("Not yet"), "\n".join(found))
            return
        self.accept()

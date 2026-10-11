"""Payees, for the desktop: browse them, and change them if you may write.

Who the money went to or came from. Editors and owners add, edit, merge
duplicates and delete the unused; the server refuses to delete one a
transaction still names, and says so in the status line.

Like the web app, an edit is one sparse patch: only the fields that actually
changed travel. Merging is not an edit but a disappearance -- the source is
repointed and gone -- so it stands alone in the form and says so before it
happens. Every payee also leads to the transactions naming it.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    PayeeIn,
    PayeeOut,
    PayeePatch,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import LedgerClient
from ..i18n import _
from .main_window import Executor, ThreadedExecutor

__all__ = ["PayeeForm", "PayeesDialog"]


def match_keys_list(text: str) -> list[str]:
    """'ACME, Acme BV' -- other spellings, comma-separated, blanks dropped."""
    return [part.strip() for part in text.split(",") if part.strip()]


class PayeeForm(QDialog):
    """Create a payee, or edit one. Collects fields; performs nothing."""

    def __init__(
        self,
        payees: list[PayeeOut],
        accounts: list[AccountOut],
        *,
        existing: PayeeOut | None = None,
        on_merge: Callable[[UUID, UUID], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.payees = list(payees)
        self.accounts = list(accounts)
        self.existing = existing
        self.on_merge = on_merge
        self.merged = False
        self.setWindowTitle(
            _("New payee") if existing is None else f"{_('Edit')} {existing.name}"
        )
        self.setMinimumWidth(520)

        self.name = QLineEdit(existing.name if existing else "")
        self.default_account = QComboBox()
        self.default_account.addItem("—", None)
        for account in sorted(
            (a for a in self.accounts if not a.placeholder and not a.closed),
            key=lambda a: a.path,
        ):
            self.default_account.addItem(account.path, str(account.id))
        if existing is not None and existing.default_account_id is not None:
            self.default_account.setCurrentIndex(
                self.default_account.findData(str(existing.default_account_id))
            )
        self.default_account.setToolTip(_("Pre-filled when you post to this payee."))

        self.match_keys = QLineEdit(", ".join(existing.match_keys) if existing else "")
        self.match_keys.setToolTip(
            _("Other spellings, comma-separated; an import uses them to find this payee.")
        )
        self.address = QLineEdit(existing.address if existing else "")
        self.notes = QLineEdit(existing.notes if existing else "")

        self.hint = QLabel("")
        self.hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow(_("Name"), self.name)
        form.addRow(_("Default account"), self.default_account)
        form.addRow(_("Also matches"), self.match_keys)
        form.addRow(_("Address"), self.address)
        form.addRow(_("Notes"), self.notes)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        if existing is not None and on_merge is not None:
            targets = sorted(
                (p for p in self.payees if p.id != existing.id),
                key=lambda p: p.name.casefold(),
            )
            if targets:
                layout.addWidget(self._merge_box(targets))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save = QPushButton(_("Create payee") if existing is None else _("Save"))
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self.name.textChanged.connect(self._check)
        self._check()

    def _check(self) -> None:
        """Say what the server would say, before it says it."""
        name = self.name.text().strip()
        clash = None
        if name:
            for p in self.payees:
                if self.existing is not None and p.id == self.existing.id:
                    continue
                if p.name.strip().casefold() == name.casefold():
                    clash = p
                    break
        self.hint.setText(
            _("{name} already exists.").format(name=clash.name) if clash else ""
        )
        self.save.setEnabled(bool(name) and clash is None)

    def _save(self) -> None:
        if not self.name.text().strip() or not self.save.isEnabled():
            return
        self.accept()

    # -- merge -----------------------------------------------------------
    def _merge_box(self, targets: list[PayeeOut]) -> QGroupBox:
        box = QGroupBox(_("Merge into"))
        layout = QVBoxLayout(box)
        self.merge_target = QComboBox()
        self.merge_target.addItem(_("— choose a payee —"), None)
        for p in targets:
            self.merge_target.addItem(p.name, str(p.id))
        self.merge_hint = QLabel(
            _(
                "Every transaction naming this payee is moved to the chosen one; "
                "this one disappears. Its spellings are kept as matches."
            )
        )
        self.merge_hint.setWordWrap(True)
        self.merge_confirm = QLabel("")
        self.merge_confirm.setWordWrap(True)
        self.merge_button = QPushButton(_("Merge…"))
        self.merge_button.setEnabled(False)
        self.merge_yes = QPushButton(_("Yes, merge"))
        self.merge_yes.clicked.connect(self._do_merge)
        self.merge_no = QPushButton(_("No"))
        self.merge_no.clicked.connect(self._hide_confirm)
        for button in (self.merge_yes, self.merge_no):
            button.setVisible(False)
        self.merge_target.currentIndexChanged.connect(self._offer_merge)
        self.merge_button.clicked.connect(self._ask_merge)
        layout.addWidget(self.merge_target)
        layout.addWidget(self.merge_hint)
        layout.addWidget(self.merge_button)
        layout.addWidget(self.merge_confirm)
        row = QHBoxLayout()
        row.addWidget(self.merge_yes)
        row.addWidget(self.merge_no)
        layout.addLayout(row)
        return box

    def _offer_merge(self, index: int) -> None:
        self.merge_button.setEnabled(index > 0)
        self._hide_confirm()

    def _hide_confirm(self) -> None:
        for button in (self.merge_yes, self.merge_no):
            button.setVisible(False)
        self.merge_confirm.setText("")

    def _ask_merge(self) -> None:
        existing = self.existing
        assert existing is not None
        data = self.merge_target.currentData()
        if not isinstance(data, str):
            return
        target = next((p for p in self.payees if p.id == UUID(data)), None)
        if target is None:
            return
        self.merge_confirm.setText(
            _("Merge {source} into {target}? There is no undo.").format(
                source=existing.name, target=target.name
            )
        )
        for button in (self.merge_yes, self.merge_no):
            button.setVisible(True)

    def _do_merge(self) -> None:
        """The payee disappears, so this is not an edit: do it, then close."""
        if self.existing is None or self.on_merge is None:
            return
        data = self.merge_target.currentData()
        if isinstance(data, str):
            self.on_merge(self.existing.id, UUID(data))
            self.merged = True
        self.accept()

    # -- what the dialog is asked for -------------------------------------
    def to_create(self) -> PayeeIn:
        data = self.default_account.currentData()
        return PayeeIn(
            name=self.name.text().strip(),
            notes=self.notes.text().strip(),
            address=self.address.text().strip(),
            match_keys=match_keys_list(self.match_keys.text()),
            default_account_id=UUID(data) if isinstance(data, str) else None,
        )

    def to_patch(self) -> PayeePatch:
        """Only what changed: the rest stays home."""
        existing = self.existing
        assert existing is not None
        patch: dict[str, object] = {}
        name = self.name.text().strip()
        if name != existing.name:
            patch["name"] = name
        notes = self.notes.text().strip()
        if notes != existing.notes:
            patch["notes"] = notes
        address = self.address.text().strip()
        if address != existing.address:
            patch["address"] = address
        keys = match_keys_list(self.match_keys.text())
        if keys != list(existing.match_keys):
            patch["match_keys"] = keys
        data = self.default_account.currentData()
        default_account_id = UUID(data) if isinstance(data, str) else None
        if default_account_id != existing.default_account_id:
            patch["default_account_id"] = default_account_id
        return PayeePatch.model_validate(patch)


class PayeesDialog(QDialog):
    """Every payee of the book, alphabetical. Editors and owners may change them."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        accounts: list[AccountOut],
        *,
        can_edit: bool,
        on_changed: Callable[[], None] | None = None,
        on_show_transactions: Callable[..., None] | None = None,
        run_dialog: Callable[[QDialog], int] | None = None,
        executor: Executor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.accounts = list(accounts)
        self.can_edit = can_edit
        self.on_changed = on_changed
        self.on_show_transactions = on_show_transactions
        self.run_dialog = run_dialog or (lambda dialog: dialog.exec())
        self.executor = executor or ThreadedExecutor()
        self.payees: list[PayeeOut] = []
        self._selected: PayeeOut | None = None
        self.changed = False

        self.setWindowTitle(_("Payees"))
        self.resize(760, 520)

        self.blurb = QLabel(
            _(
                "Who the money went to or came from. Merge duplicates; a payee "
                "no transaction names can be deleted."
            )
            if can_edit
            else _(
                "Who the money went to or came from. Editors and owners can change them."
            )
        )
        self.blurb.setWordWrap(True)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText(_("Filter payees"))
        self.filter.textChanged.connect(self._fill)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [_("Payee"), _("Also matches"), _("Default account"), _("Notes")]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemDoubleClicked.connect(self.edit_payee)
        self.table.itemSelectionChanged.connect(self._chosen)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.empty = QLabel("")
        self.empty.setWordWrap(True)

        self.add_button = QPushButton(_("Add…"))
        self.add_button.clicked.connect(self.add_payee)
        self.edit_button = QPushButton(_("Edit"))
        self.edit_button.clicked.connect(self.edit_payee)
        self.delete_button = QPushButton(_("Delete"))
        self.delete_button.setToolTip(
            _(
                "Only a payee no transaction names can be deleted; merge it into "
                "another otherwise."
            )
        )
        self.delete_button.clicked.connect(self.delete_payee)
        self.transactions_button = QPushButton(_("Transactions…"))
        self.transactions_button.setToolTip(_("Transactions with this payee"))
        self.transactions_button.clicked.connect(self.show_transactions)
        close = QPushButton(_("Close"))
        close.clicked.connect(self.accept)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.transactions_button)
        buttons.addStretch(1)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.blurb)
        layout.addWidget(self.filter)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty)
        layout.addLayout(buttons)

        self.reload()

    # -- the list ----------------------------------------------------------
    def reload(self) -> None:
        try:
            self.payees = self.client.payees(self.book_id)
        except Exception as exc:  # the main window reports in detail
            self.status.setText(f"{_('Could not open the book')}: {exc}")
            self.payees = []
        self._fill()
        self._update_buttons()

    def _fill(self) -> None:
        keep = self._selected.id if self._selected else None
        needle = self.filter.text().strip().casefold()
        rows = sorted(
            (
                p
                for p in self.payees
                if not needle
                or needle in p.name.casefold()
                or any(needle in key.casefold() for key in p.match_keys)
            ),
            key=lambda p: p.name.casefold(),
        )
        accounts = {a.id: a for a in self.accounts}
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for i, payee in enumerate(rows):
            account = (
                accounts.get(payee.default_account_id)
                if payee.default_account_id
                else None
            )
            cells = [
                payee.name,
                ", ".join(payee.match_keys),
                account.path if account else "",
                payee.notes,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, payee.id)
                self.table.setItem(i, column, item)
        self.table.blockSignals(False)
        if rows:
            self.empty.setText("")
        elif not self.payees:
            self.empty.setText(
                _(
                    "No payees yet. One is created the first time you name one "
                    "on a transaction."
                )
            )
        else:
            self.empty.setText(_("Nothing matches."))
        if keep is not None:
            for i in range(self.table.rowCount()):
                cell = self.table.item(i, 0)
                if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == keep:
                    self.table.setCurrentCell(i, 0)
                    break
        self._chosen()

    def _chosen(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        raw = item.data(Qt.ItemDataRole.UserRole) if item else None
        self._selected = next((p for p in self.payees if p.id == raw), None)
        self._update_buttons()

    def _update_buttons(self) -> None:
        chosen = self._selected is not None
        self.add_button.setEnabled(self.can_edit)
        self.edit_button.setEnabled(self.can_edit and chosen)
        self.delete_button.setEnabled(self.can_edit and chosen)
        self.transactions_button.setEnabled(
            chosen and self.on_show_transactions is not None
        )

    # -- writing -----------------------------------------------------------
    def _form(self, existing: PayeeOut | None = None) -> PayeeForm:
        return PayeeForm(
            self.payees,
            self.accounts,
            existing=existing,
            on_merge=self.merge if existing is not None else None,
            parent=self,
        )

    def _run(self, work: Callable[[], object], title: str) -> None:
        def done(_result: object) -> None:
            self.changed = True
            self.status.setText("")
            self.reload()
            if self.on_changed is not None:
                self.on_changed()

        self.executor.run(work, done, self._failed(title))

    def _failed(self, title: str) -> Callable[[Exception], None]:
        def handle(exc: Exception) -> None:
            # Whatever went through before the refusal did, so re-read either way.
            self.status.setText(f"{title}: {exc}")
            self.reload()
            if self.on_changed is not None:
                self.on_changed()

        return handle

    def add_payee(self) -> None:
        if not self.can_edit:
            return
        form = self._form()
        if self.run_dialog(form) != QDialog.DialogCode.Accepted:
            return
        body = form.to_create()
        self._run(
            lambda: self.client.create_payee(self.book_id, body),
            _("Could not save"),
        )

    def edit_payee(self) -> None:
        payee = self._selected
        if payee is None or not self.can_edit:
            return
        form = self._form(payee)
        if self.run_dialog(form) != QDialog.DialogCode.Accepted:
            return
        if form.merged:
            # A disappearance, not an edit: the merge has already run.
            return
        self._save(payee, form)

    def _save(self, existing: PayeeOut, form: PayeeForm) -> None:
        patch = form.to_patch()
        if not patch.model_fields_set:
            return
        self._run(
            lambda: self.client.update_payee(self.book_id, existing.id, patch),
            _("Could not save"),
        )

    def merge(self, source_id: UUID, target_id: UUID) -> None:
        """Not an edit but a disappearance; the form has already confirmed it."""
        if not self.can_edit:
            return
        self._run(
            lambda: self.client.merge_payees(self.book_id, target_id, [source_id]),
            _("Could not save"),
        )

    def delete_payee(self) -> None:
        payee = self._selected
        if payee is None or not self.can_edit:
            return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            _("Payees"),
            _("Delete {name}?").format(name=payee.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run(
            lambda: self.client.delete_payee(self.book_id, payee.id),
            _("Could not delete"),
        )

    # -- reading ------------------------------------------------------------
    def show_transactions(self) -> None:
        payee = self._selected
        if payee is None or self.on_show_transactions is None:
            return
        self.on_show_transactions(payee_id=payee.id, parent=self)

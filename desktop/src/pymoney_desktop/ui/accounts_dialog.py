"""The chart of accounts, for the desktop: browse it, and change it if you own it.

Everyone can read it; only an owner gets the controls. Renaming, moving, closing
or deleting an account reshapes every report the household shares, so the server
answers 403 to anyone else -- the buttons follow suit, and the server has the
last word either way.

The tree is grouped by class first (assets, liabilities, income, expenses,
equity), then by the account hierarchy under it. A parent and its children are
always the same class, so the grouping holds whatever shape the tree takes.

Like the web app, an edit is not one command: each kind of change goes to its own
endpoint -- rename, move, patch, close -- in that order, stopping at the first
the server refuses, so whatever did go through stays consistent. Merging is not
an edit but a disappearance, so it stands alone and says so before it happens.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from pymoney.api.schemas import (
    AccountIn,
    AccountOut,
    AccountPatch,
    CommodityOut,
)
from pymoney.core.account import AccountType
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..client import LedgerClient
from ..i18n import _
from .main_window import Executor, ThreadedExecutor

__all__ = ["AccountForm", "AccountsDialog"]

# Every type, under the class it belongs to; the server enforces the pairing.
CLASS_ORDER = ("ASSET", "LIABILITY", "INCOME", "EXPENSE", "EQUITY")
CLASS_LABELS = {
    "ASSET": "Assets",
    "LIABILITY": "Liabilities",
    "INCOME": "Income",
    "EXPENSE": "Expenses",
    "EQUITY": "Equity",
}
TYPE_LABELS = {
    "CHECKING": "Checking",
    "SAVINGS": "Savings",
    "CASH": "Cash",
    "INVESTMENT": "Investment",
    "STOCK": "Stock / security",
    "RECEIVABLE": "Receivable",
    "ASSET": "Other asset",
    "CREDIT_CARD": "Credit card",
    "LOAN": "Loan",
    "PAYABLE": "Payable",
    "LIABILITY": "Other liability",
    "INCOME": "Income",
    "EXPENSE": "Expense",
    "EQUITY": "Equity",
}
CLASS_OF_TYPE = {
    "CHECKING": "ASSET",
    "SAVINGS": "ASSET",
    "CASH": "ASSET",
    "INVESTMENT": "ASSET",
    "STOCK": "ASSET",
    "RECEIVABLE": "ASSET",
    "ASSET": "ASSET",
    "CREDIT_CARD": "LIABILITY",
    "LOAN": "LIABILITY",
    "PAYABLE": "LIABILITY",
    "LIABILITY": "LIABILITY",
    "INCOME": "INCOME",
    "EXPENSE": "EXPENSE",
    "EQUITY": "EQUITY",
}
TOP_LEVEL = "— top level —"


def type_label(account_type: str) -> str:
    """A readable, translated type name; the wire name if one is unknown."""
    return _(TYPE_LABELS.get(account_type, account_type))


def descendant_ids(accounts: list[AccountOut], root_id: UUID) -> set[UUID]:
    """The account and everything under it: it cannot become its own parent."""
    out = {root_id}
    grew = True
    while grew:
        grew = False
        for a in accounts:
            if a.parent_id in out and a.id not in out:
                out.add(a.id)
                grew = True
    return out


class AccountForm(QDialog):
    """Create an account, or edit one. Collects fields; performs nothing."""

    def __init__(
        self,
        accounts: list[AccountOut],
        commodities: list[CommodityOut],
        *,
        existing: AccountOut | None = None,
        base_currency_id: UUID | None = None,
        default_parent_id: UUID | None = None,
        on_merge: Callable[[UUID, UUID], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.accounts = list(accounts)
        self.commodities = list(commodities)
        self.existing = existing
        self.on_merge = on_merge
        self.setWindowTitle(
            _("New account") if existing is None else f"{_('Edit')} {existing.path}"
        )
        self.setMinimumWidth(520)

        editing = existing is not None

        self.name = QLineEdit(existing.name if existing else "")
        self.type = QComboBox()
        for group in CLASS_ORDER:
            for code, label in TYPE_LABELS.items():
                if CLASS_OF_TYPE[code] == group:
                    self.type.addItem(_(label), code)
        # The type fixes the class, and with it the tree the account joins, so it
        # cannot change once the account exists.
        self.type.setEnabled(existing is None)
        if existing is not None:
            self.type.setCurrentIndex(self.type.findData(existing.type))
        else:
            self.type.setCurrentIndex(self.type.findData("CHECKING"))

        self.parent_box = QComboBox()
        self.commodity = QComboBox()
        for c in self.commodities:
            self.commodity.addItem(
                f"{c.code} — {c.name}" if c.name and c.name != c.code else c.code,
                str(c.id),
            )
        # A commodity change would reinterpret every historical amount, so the
        # server refuses it once money has been posted; the web fixes it outright.
        self.commodity.setEnabled(existing is None)
        if existing is not None:
            self.commodity.setCurrentIndex(
                self.commodity.findData(str(existing.commodity_id))
            )
        elif base_currency_id is not None:
            self.commodity.setCurrentIndex(self.commodity.findData(str(base_currency_id)))

        self.description = QLineEdit(existing.description if existing else "")
        self.code = QLineEdit(existing.code if existing else "")
        self.institution = QLineEdit(existing.institution if existing else "")
        self.iban = QLineEdit(existing.iban if existing else "")
        self.notes = QLineEdit(existing.notes if existing else "")
        self.placeholder = QCheckBox(_("Placeholder"))
        self.placeholder.setChecked(existing.placeholder if existing else False)
        self.closed = QCheckBox(_("Closed"))
        self.closed.setChecked(existing.closed if existing else False)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow(_("Name"), self.name)
        form.addRow(_("Type"), self.type)
        form.addRow(_("Parent"), self.parent_box)
        form.addRow(_("Commodity"), self.commodity)
        form.addRow(_("Description"), self.description)
        form.addRow(_("Code"), self.code)
        form.addRow(_("Institution"), self.institution)
        form.addRow(_("IBAN"), self.iban)
        form.addRow(_("Notes"), self.notes)
        form.addRow("", self.placeholder)
        if editing:
            form.addRow("", self.closed)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        if editing and on_merge is not None:
            layout.addWidget(self._merge_box())
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.save = QPushButton(_("Save"))
        self.save.setDefault(True)
        self.save.clicked.connect(self._save)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(self.save)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self.type.currentIndexChanged.connect(self._fill_parents)
        self.name.textChanged.connect(self._check)
        if existing is not None:
            self._fill_parents()
        else:
            self._fill_parents(preferred=default_parent_id)

    # -- the parent list, which depends on the class --------------------
    def _candidates(self) -> list[AccountOut]:
        cls = self._cls()
        excluded = (
            descendant_ids(self.accounts, self.existing.id) if self.existing else set()
        )
        return sorted(
            (
                a
                for a in self.accounts
                if a.account_class == cls and a.id not in excluded and not a.closed
            ),
            key=lambda a: a.path,
        )

    def _cls(self) -> str:
        if self.existing is not None:
            return self.existing.account_class
        return CLASS_OF_TYPE.get(str(self.type.currentData()), "ASSET")

    def _fill_parents(self, *, preferred: UUID | None = None) -> None:
        current = self.parent_box.currentData()
        self.parent_box.blockSignals(True)
        self.parent_box.clear()
        self.parent_box.addItem(_(TOP_LEVEL), None)
        for a in self._candidates():
            self.parent_box.addItem(a.path, str(a.id))
        wanted = preferred if preferred is not None else current
        index = self.parent_box.findData(wanted)
        self.parent_box.setCurrentIndex(index if index >= 0 else 0)
        self.parent_box.blockSignals(False)
        self._check()

    def _check(self) -> None:
        """Say what the server would say, before it says it."""
        name = self.name.text().strip()
        clash = None
        if name:
            wanted = self._parent_id()
            for a in self.accounts:
                if self.existing is not None and a.id == self.existing.id:
                    continue
                if a.parent_id == wanted and a.name.strip().casefold() == name.casefold():
                    clash = a
                    break
        if clash is not None:
            self.hint.setText(
                _("{account} already exists here.").format(account=clash.path)
            )
        elif self.existing is not None and self._parent_id() != self.existing.parent_id:
            self.hint.setText(_("Moving keeps every posting; only the path changes."))
        else:
            self.hint.setText("")
        self.save.setEnabled(bool(name) and clash is None)

    def _parent_id(self) -> UUID | None:
        data = self.parent_box.currentData()
        return UUID(data) if isinstance(data, str) and data else None

    def _save(self) -> None:
        if not self.name.text().strip() or not self.save.isEnabled():
            return
        self.accept()

    # -- merge -----------------------------------------------------------
    def _merge_box(self) -> QGroupBox:
        existing = self.existing
        assert existing is not None
        excluded = descendant_ids(self.accounts, existing.id)
        targets = sorted(
            (
                a
                for a in self.accounts
                if a.id != existing.id
                and a.account_class == existing.account_class
                and a.commodity_id == existing.commodity_id
                and a.id not in excluded
                and not a.placeholder
                and not a.closed
            ),
            key=lambda a: a.path,
        )
        box = QGroupBox(_("Merge into"))
        layout = QVBoxLayout(box)
        if not targets:
            layout.addWidget(
                QLabel(_("There is no other account to merge this one into."))
            )
            return box
        self.merge_target = QComboBox()
        self.merge_target.addItem(_("— choose an account —"), None)
        for a in targets:
            self.merge_target.addItem(a.path, str(a.id))
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
        target = next((a for a in self.accounts if a.id == UUID(data)), None)
        if target is None:
            return
        self.merge_confirm.setText(
            _("Merge {source} into {target}? There is no undo.").format(
                source=existing.path, target=target.path
            )
        )
        for button in (self.merge_yes, self.merge_no):
            button.setVisible(True)

    def _do_merge(self) -> None:
        """The account disappears, so this is not an edit: do it, then close."""
        if self.existing is None or self.on_merge is None:
            return
        data = self.merge_target.currentData()
        if isinstance(data, str):
            self.on_merge(self.existing.id, UUID(data))
        self.accept()

    # -- what the dialog is asked for -------------------------------------
    def to_create(self) -> AccountIn:
        return AccountIn(
            name=self.name.text().strip(),
            type=AccountType(str(self.type.currentData())),
            parent_id=self._parent_id(),
            commodity_id=self.commodity.currentData(),
            description=self.description.text().strip(),
            code=self.code.text().strip(),
            placeholder=self.placeholder.isChecked(),
            notes=self.notes.text().strip(),
            institution=self.institution.text().strip(),
            iban=self.iban.text().strip(),
        )


def _row(account: AccountOut) -> QTreeWidgetItem:
    """One account: name, type and commodity, with its flags spelled out."""
    flags = [
        flag
        for flag in (
            _("placeholder") if account.placeholder else "",
            _("closed") if account.closed else "",
        )
        if flag
    ]
    label = account.name + (f" ({', '.join(flags)})" if flags else "")
    item = QTreeWidgetItem([label, type_label(str(account.type)), account.commodity])
    item.setData(0, Qt.ItemDataRole.UserRole, account.id)
    return item


def _add_branch(
    root: QTreeWidgetItem,
    group: str,
    by_parent: dict[UUID | None, list[AccountOut]],
    found: dict[UUID, QTreeWidgetItem],
) -> None:
    """Every account of ``group``, children under their parent, name-ordered."""

    def add(parent: QTreeWidgetItem, parent_id: UUID | None) -> None:
        for a in sorted(by_parent.get(parent_id, []), key=lambda x: x.name.casefold()):
            item = _row(a)
            parent.addChild(item)
            found[a.id] = item
            add(item, a.id)

    # Only the roots of the class are filtered: a child is always the same class
    # as its parent, so the grouping holds however deep the tree goes.
    for a in sorted(
        (a for a in by_parent.get(None, []) if a.account_class == group),
        key=lambda x: x.name.casefold(),
    ):
        item = _row(a)
        root.addChild(item)
        found[a.id] = item
        add(item, a.id)


class AccountsDialog(QDialog):
    """The chart of accounts, grouped by class. Owners may change it."""

    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        commodities: list[CommodityOut],
        *,
        can_edit: bool,
        base_currency_id: UUID | None = None,
        on_changed: Callable[[], None] | None = None,
        run_dialog: Callable[[QDialog], int] | None = None,
        executor: Executor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.commodities = list(commodities)
        self.can_edit = can_edit
        self.base_currency_id = base_currency_id
        self.on_changed = on_changed
        self.run_dialog = run_dialog or (lambda dialog: dialog.exec())
        self.executor = executor or ThreadedExecutor()
        self.accounts: list[AccountOut] = []
        self._selected: AccountOut | None = None
        self.changed = False

        self.setWindowTitle(_("Accounts"))
        self.resize(760, 520)

        self.blurb = QLabel(
            _(
                "The chart of accounts. Changes here reshape every report, so only "
                "owners may make them."
            )
            if can_edit
            else _("The chart of accounts. Only the book's owners can change it.")
        )
        self.blurb.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([_("Account"), _("Type"), _("Commodity")])
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemDoubleClicked.connect(self.edit_account)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tree.currentItemChanged.connect(lambda current, _: self._chosen(current))

        self.add_button = QPushButton(_("Add…"))
        self.add_button.clicked.connect(self.add_account)
        self.edit_button = QPushButton(_("Edit"))
        self.edit_button.clicked.connect(self.edit_account)
        self.delete_button = QPushButton(_("Delete"))
        self.delete_button.setToolTip(
            _(
                "Only an account with no postings and no sub-accounts can be deleted; "
                "merge or move them first."
            )
        )
        self.delete_button.clicked.connect(self.delete_account)
        close = QPushButton(_("Close"))
        close.clicked.connect(self.accept)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.blurb)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

        self.reload()

    # -- the tree ---------------------------------------------------------
    def reload(self) -> None:
        """Read the accounts again. The class roots are always in this order."""
        try:
            self.accounts = self.client.accounts(self.book_id)
        except Exception as exc:  # the main window reports in detail
            self.status.setText(f"{_('Could not open the book')}: {exc}")
            self.accounts = []
        self._fill()
        self._update_buttons()

    def _fill(self) -> None:
        keep = self._selected.id if self._selected else None
        self.tree.blockSignals(True)
        self.tree.clear()
        by_parent: dict[UUID | None, list[AccountOut]] = {}
        for a in self.accounts:
            by_parent.setdefault(a.parent_id, []).append(a)
        found: dict[UUID, QTreeWidgetItem] = {}
        for group in CLASS_ORDER:
            root = QTreeWidgetItem([_(CLASS_LABELS[group]), "", ""])
            font = root.font(0)
            font.setBold(True)
            for column in range(3):
                root.setFont(column, font)
            self.tree.addTopLevelItem(root)
            _add_branch(root, group, by_parent, found)
            root.setExpanded(True)
        self.tree.blockSignals(False)
        if keep is not None and keep in found:
            self.tree.setCurrentItem(found[keep])
        self._chosen(self.tree.currentItem())

    def _chosen(self, item: QTreeWidgetItem | None) -> None:
        raw = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self._selected = next((a for a in self.accounts if a.id == raw), None)
        self._update_buttons()

    def _update_buttons(self) -> None:
        # A class root is not an account: it can be read, never edited.
        chosen = self._selected is not None
        self.add_button.setEnabled(self.can_edit)
        self.edit_button.setEnabled(self.can_edit and chosen)
        self.delete_button.setEnabled(self.can_edit and chosen)

    # -- writing ----------------------------------------------------------
    def _form(
        self, existing: AccountOut | None = None, *, prefer_parent: bool = False
    ) -> AccountForm:
        # Adding under the selected account is the usual next step, so start there.
        default_parent = self._selected.id if prefer_parent and self._selected else None
        return AccountForm(
            self.accounts,
            self.commodities,
            existing=existing,
            base_currency_id=self.base_currency_id,
            default_parent_id=default_parent,
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

    def add_account(self) -> None:
        if not self.can_edit:
            return
        form = self._form(prefer_parent=True)
        if self.run_dialog(form) != QDialog.DialogCode.Accepted:
            return
        body = form.to_create()
        self._run(
            lambda: self.client.create_account(self.book_id, body),
            _("Could not save"),
        )

    def edit_account(self) -> None:
        account = self._selected
        if account is None or not self.can_edit:
            return
        form = self._form(account)
        if self.run_dialog(form) != QDialog.DialogCode.Accepted:
            return
        self._save(account, form)

    def _save(self, existing: AccountOut, form: AccountForm) -> None:
        """Each kind of change is its own endpoint, in this order, and stops at
        the first the server refuses -- so what did go through stays consistent."""
        name = form.name.text().strip()
        parent_id = form._parent_id()
        patch: dict[str, str | bool] = {}
        for field, value in (
            ("description", form.description.text().strip()),
            ("code", form.code.text().strip()),
            ("notes", form.notes.text().strip()),
            ("institution", form.institution.text().strip()),
            ("iban", form.iban.text().strip()),
            ("placeholder", form.placeholder.isChecked()),
        ):
            if value != getattr(existing, field):
                patch[field] = value

        steps: list[Callable[[], object]] = []
        if name != existing.name:
            steps.append(
                lambda: self.client.rename_account(self.book_id, existing.id, name)
            )
        if parent_id != existing.parent_id:
            steps.append(
                lambda: self.client.move_account(self.book_id, existing.id, parent_id)
            )
        if patch:
            body = AccountPatch.model_validate(patch)
            steps.append(
                lambda: self.client.patch_account(self.book_id, existing.id, body)
            )
        if form.closed.isChecked() != existing.closed:
            steps.append(
                lambda: self.client.set_account_closed(
                    self.book_id, existing.id, form.closed.isChecked()
                )
            )
        if not steps:
            return

        def work() -> None:
            for step in steps:
                step()

        self._run(work, _("Could not save"))

    def merge(self, source_id: UUID, target_id: UUID) -> None:
        """Not an edit but a disappearance; the form has already confirmed it."""
        if not self.can_edit:
            return
        self._run(
            lambda: self.client.merge_accounts(self.book_id, target_id, source_id),
            _("Could not save"),
        )

    def delete_account(self) -> None:
        account = self._selected
        if account is None or not self.can_edit:
            return
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self,
            _("Accounts"),
            _("Delete {name}?").format(name=account.path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run(
            lambda: self.client.delete_account(self.book_id, account.id),
            _("Could not delete"),
        )

"""Where is the server, who are you, and do you want a local copy."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...i18n import LANGUAGE_NAMES, SUPPORTED
from ..i18n import _, current_locale, set_locale

__all__ = ["LoginDialog", "default_replica_path"]


def default_replica_path() -> Path:
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    ) or str(Path.home() / ".pymoney")
    return Path(base) / "replica.sqlite"


class LoginDialog(QDialog):
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:8000",
        email: str = "",
        offline: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("PyMoney — log in"))
        form = QFormLayout()
        self.url = QLineEdit(url)
        form.addRow(_("Server"), self.url)
        self.email = QLineEdit(email)
        form.addRow(_("Email"), self.email)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("Password"), self.password)
        self.language = QComboBox()
        for code in SUPPORTED:
            self.language.addItem(LANGUAGE_NAMES[code], code)
        self.language.setCurrentIndex(max(0, self.language.findData(current_locale())))
        # Relabel the dialog in the chosen language at once, so the choice is visible.
        self.language.currentIndexChanged.connect(lambda _i: self._relanguage())
        form.addRow(_("Language"), self.language)
        self.offline = QCheckBox(_("Keep a local copy and work offline"))
        self.offline.setChecked(offline)
        form.addRow("", self.offline)
        hint = QLabel(
            _(
                "With a local copy, reading never needs the server and changes are queued and sent when it is "
                "reachable; the server has the final say and refuses what no longer fits. Without one, every "
                "change is a request and fails the moment the network does."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        self.error = QLabel()
        self.error.setStyleSheet("color: #b91c1c")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Log in"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._form, self._buttons, self._hint = form, buttons, hint

    def chosen_locale(self) -> str:
        return str(self.language.currentData())

    def _relanguage(self) -> None:
        set_locale(self.chosen_locale())
        self.setWindowTitle(_("PyMoney — log in"))
        for row, key in enumerate(("Server", "Email", "Password", "Language")):
            label = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            if label is not None and isinstance(label.widget(), QLabel):
                label.widget().setText(_(key))  # type: ignore[union-attr]
        self.offline.setText(_("Keep a local copy and work offline"))
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Log in"))
        self._hint.setText(
            _(
                "With a local copy, reading never needs the server and changes are queued and sent when it is "
                "reachable; the server has the final say and refuses what no longer fits. Without one, every "
                "change is a request and fails the moment the network does."
            )
        )

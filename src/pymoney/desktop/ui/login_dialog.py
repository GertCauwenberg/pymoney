"""Where is the server, who are you, and do you want a local copy."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

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
        self.setWindowTitle("PyMoney — log in")
        form = QFormLayout()
        self.url = QLineEdit(url)
        form.addRow("Server", self.url)
        self.email = QLineEdit(email)
        form.addRow("Email", self.email)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password", self.password)
        self.offline = QCheckBox("Keep a local copy and work offline")
        self.offline.setChecked(offline)
        form.addRow("", self.offline)
        hint = QLabel(
            "With a local copy, reading never needs the server and changes are queued and sent when it is "
            "reachable; the server has the final say and refuses what no longer fits. Without one, every "
            "change is a request and fails the moment the network does."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        self.error = QLabel()
        self.error.setStyleSheet("color: #b91c1c")
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Log in")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

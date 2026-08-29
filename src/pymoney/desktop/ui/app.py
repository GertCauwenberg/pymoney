"""Putting the window together: log in, choose a client, show the books."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from ..client import ApiError, LedgerClient, OfflineError
from ..offline import OfflineClient
from ..remote import RemoteClient
from ..transport import UrllibTransport
from .login_dialog import LoginDialog, default_replica_path
from .main_window import MainWindow

__all__ = ["connect", "main"]


def connect(url: str, *, offline: bool, replica: Path | None = None) -> LedgerClient:
    transport = UrllibTransport(url)
    if offline:
        path = replica or default_replica_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return OfflineClient(transport, path)
    return RemoteClient(transport)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("PyMoney")
    app.setOrganizationName("PyMoney")
    settings = QSettings()
    url = str(settings.value("server", "http://127.0.0.1:8000"))
    email = str(settings.value("email", ""))
    offline = bool(settings.value("offline", True, type=bool))

    # A local copy may already hold a session: try it before asking.
    client: LedgerClient | None = None
    if offline:
        candidate = connect(url, offline=True)
        try:
            if candidate.me() is not None:
                client = candidate
        except (ApiError, OfflineError):
            pass
        if client is None:
            candidate.close()

    while client is None:
        dialog = LoginDialog(url=url, email=email, offline=offline)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
        url, email, offline = (
            dialog.url.text().strip(),
            dialog.email.text().strip(),
            dialog.offline.isChecked(),
        )
        candidate = connect(url, offline=offline)
        try:
            candidate.login(email, dialog.password.text())
            client = candidate
        except (ApiError, OfflineError) as exc:
            candidate.close()
            dialog.error.setText(str(exc))
            # Re-show with the message: loop again with the same values.
            retry = LoginDialog(url=url, email=email, offline=offline)
            retry.error.setText(str(exc))
            if retry.exec() != QDialog.DialogCode.Accepted:
                return 0
            url, email, offline = (
                retry.url.text().strip(),
                retry.email.text().strip(),
                retry.offline.isChecked(),
            )
            candidate = connect(url, offline=offline)
            try:
                candidate.login(email, retry.password.text())
                client = candidate
            except (ApiError, OfflineError):
                candidate.close()
                continue
    settings.setValue("server", url)
    settings.setValue("email", email)
    settings.setValue("offline", offline)

    window = MainWindow(client)
    window.logout_button.clicked.connect(lambda: (client.logout(), app.quit()))
    window.show()
    window.load()
    try:
        return app.exec()
    finally:
        client.close()

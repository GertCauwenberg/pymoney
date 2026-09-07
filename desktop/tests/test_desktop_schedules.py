"""The desktop Scheduled… manager and the Due dialog, offscreen, real API."""

from __future__ import annotations

import os
from uuid import UUID

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pymoney.api.testing import PASSWORD, Client
from PySide6.QtWidgets import QDialog

from pymoney_desktop import OfflineClient
from pymoney_desktop.ui.main_window import MainWindow, SyncExecutor
from pymoney_desktop.ui.schedules import DueDialog, ScheduleForm, SchedulesDialog
from tests.test_desktop_clients import EMAIL, InProcessTransport
from tests.test_desktop_ui import select_account


@pytest.fixture
def window(api: Client, tmp_path, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    boxes: list[tuple[str, str]] = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(
                lambda parent, title, text, *a, **k: boxes.append((title, text))
            ),
        )
    api.register(EMAIL)
    book_id = api.make_book("Desk")
    chk = api.account("Checking", "CHECKING")
    rent = api.account("Rent", "EXPENSE")
    api.logout()
    client = OfflineClient(InProcessTransport(api.http), tmp_path / "r.sqlite")
    client.login(EMAIL, PASSWORD)
    win = MainWindow(client, executor=SyncExecutor(), sync_every_ms=0)
    win.report_error = lambda t, m: boxes.append((t, m))  # type: ignore[method-assign]
    qtbot.addWidget(win)
    win.boxes = boxes  # type: ignore[attr-defined]
    win.load()
    yield win, api, UUID(book_id), chk, rent
    client.close()


def test_create_a_schedule_and_enter_the_due_occurrence(window):
    win, _api, book_id, chk, rent = window
    select_account(win, "Checking")
    assert win.due_button.text() == "Due"

    def fill(dialog):
        if isinstance(dialog, SchedulesDialog):

            def add(form: ScheduleForm):
                form.name.setText("Rent")
                form.amount.setText("1200.00")
                form.whose.setCurrentIndex(form.whose.findData(chk["id"]))
                form.other.setCurrentIndex(form.other.findData(rent["id"]))
                form.unit.setCurrentIndex(form.unit.findData("MONTHLY"))
                from PySide6.QtCore import QDate

                form.start.setDate(QDate(2026, 1, 1))
                form._accept()
                return form.result()

            dialog.run_dialog = add
            dialog._add()
            return QDialog.DialogCode.Accepted
        if isinstance(dialog, DueDialog):
            assert dialog.list.count() >= 1
            dialog._act(post=True)
            assert dialog.posted == 1
            dialog.accept()
            return QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Accepted

    win.run_dialog = fill  # type: ignore[method-assign]
    win.open_schedules()
    assert win.client.schedules(book_id)[0].name == "Rent"
    assert win.due_button.text().startswith("Due (")

    win.open_due()
    reg = win.client.register(book_id, UUID(chk["id"]))
    assert any(r.quantity.minor == -120000 for r in reg.items)
    assert win.client.schedules(book_id)[0].occurrences_done == 1

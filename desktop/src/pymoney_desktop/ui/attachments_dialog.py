"""Receipts and invoices for one transaction.

They live server-side only: this lists what is attached, lets an editor add an
image or a PDF and remove one, and lets anyone who can see the book save a copy
locally. Calls go straight through the client -- fast against the API, and a
lost connection surfaces as an :class:`OfflineError` the dialog reports.
"""

from __future__ import annotations

import mimetypes
import tempfile
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..client import ApiError, LedgerClient, OfflineError
from ..i18n import _

__all__ = ["AttachmentsDialog"]

_ALLOWED = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/tiff",
    "application/pdf",
)


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} kB"
    return f"{n / (1024 * 1024):.1f} MB"


class AttachmentsDialog(QDialog):
    def __init__(
        self,
        client: LedgerClient,
        book_id: UUID,
        transaction_id: UUID,
        *,
        can_write: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.book_id = book_id
        self.transaction_id = transaction_id
        self.can_write = can_write
        self.setWindowTitle(_("Attachments"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        layout.addWidget(self.status)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self._open())
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        self.open_button = QPushButton(_("Open"))
        self.open_button.clicked.connect(self._open)
        self.add_button = QPushButton(_("Add…"))
        self.add_button.clicked.connect(self._add)
        self.add_button.setEnabled(can_write)
        self.save_button = QPushButton(_("Save a copy…"))
        self.save_button.clicked.connect(self._save)
        self.remove_button = QPushButton(_("Remove"))
        self.remove_button.clicked.connect(self._remove)
        self.remove_button.setEnabled(can_write)
        for b in (
            self.open_button,
            self.add_button,
            self.save_button,
            self.remove_button,
        ):
            buttons.addWidget(b)

        buttons.addStretch()
        layout.addLayout(buttons)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout.addWidget(close)

        self.list.itemSelectionChanged.connect(self._update_buttons)
        self._reload()

    # -- data ----------------------------------------------------------------
    def _reload(self) -> None:
        try:
            self.attachments = self.client.attachments(self.book_id, self.transaction_id)
        except (ApiError, OfflineError) as exc:
            self.attachments = []
            self._fail(_("Could not load the attachments"), exc)
        self.list.clear()
        for att in self.attachments:
            item = QListWidgetItem(
                f"{att.filename}  ·  {att.content_type}  ·  {_human_size(att.byte_size)}"
            )
            item.setData(256, str(att.id))
            self.list.addItem(item)
        self.status.setText(
            _("No receipts or invoices attached yet.") if not self.attachments else ""
        )
        self._update_buttons()

    def _selected_id(self) -> UUID | None:
        item = self.list.currentItem()
        return UUID(item.data(256)) if item is not None else None

    def _update_buttons(self) -> None:
        has = self.list.currentItem() is not None
        self.save_button.setEnabled(has)
        self.remove_button.setEnabled(has and self.can_write)

    # -- actions -------------------------------------------------------------
    def _add(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _("Choose a receipt or invoice"),
            "",
            _("Images and PDF (*.png *.jpg *.jpeg *.gif *.webp *.tif *.tiff *.pdf)"),
        )
        if not path:
            return
        data = Path(path).read_bytes()
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if content_type not in _ALLOWED:
            QMessageBox.warning(
                self,
                _("Not allowed"),
                _("Attach an image (JPEG, PNG, GIF, WebP, TIFF) or a PDF."),
            )
            return
        try:
            self.client.upload_attachment(
                self.book_id,
                self.transaction_id,
                filename=Path(path).name,
                data=data,
                content_type=content_type,
            )
        except (ApiError, OfflineError) as exc:
            self._fail(_("Could not add the attachment"), exc)
            return
        self._reload()

    def _save(self) -> None:
        attachment_id = self._selected_id()
        if attachment_id is None:
            return
        match = next((a for a in self.attachments if a.id == attachment_id), None)
        suggested = match.filename if match else "attachment"
        path, _filter = QFileDialog.getSaveFileName(self, _("Save a copy…"), suggested)
        if not path:
            return
        try:
            data = self.client.download_attachment(self.book_id, attachment_id)
        except (ApiError, OfflineError) as exc:
            self._fail(_("Could not download the attachment"), exc)
            return
        Path(path).write_bytes(data)

    def _remove(self) -> None:
        attachment_id = self._selected_id()
        if attachment_id is None or not self.can_write:
            return
        if (
            QMessageBox.question(self, _("Remove"), _("Remove this attachment?"))
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.client.delete_attachment(self.book_id, attachment_id)
        except (ApiError, OfflineError) as exc:
            self._fail(_("Could not remove the attachment"), exc)
            return
        self._reload()

    def _fail(self, title: str, exc: Exception) -> None:
        QMessageBox.warning(self, title, str(exc))

    def _open(self) -> None:
        attachment_id = self._selected_id()
        if attachment_id is None:
            return
        row = next((a for a in self.attachments if a.id == attachment_id), None)
        if row is None:
            return

        try:
            content = self.client.download_attachment(self.book_id, attachment_id)
        except (ApiError, OfflineError) as exc:
            self._fail(_("Could not download the attachment"), exc)
            return
        scratch = Path(tempfile.mkdtemp(prefix="pymoney-attachment-"))
        target = scratch / (Path(row.filename))
        target.write_bytes(content)
        self._update_buttons()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

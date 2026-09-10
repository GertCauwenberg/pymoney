"""The filesystem side of attachments: where the bytes actually live.

One flat file per attachment, named by its id, under a per-book subdirectory of
the configured attachments directory::

    <attachments_dir>/<book_id>/<attachment_id>

No user-supplied path component is ever used (the stored ``filename`` is
metadata only), so there is nothing to traverse out of. The directory must be
persistent -- a mounted volume in Docker -- because it is not in the database
and a plain restart would otherwise lose every receipt.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

__all__ = ["BlobStore"]


class BlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _book_dir(self, book_id: UUID) -> Path:
        return self.root / str(book_id)

    def path_for(self, book_id: UUID, attachment_id: UUID) -> Path:
        return self._book_dir(book_id) / str(attachment_id)

    def write(self, book_id: UUID, attachment_id: UUID, data: bytes) -> Path:
        directory = self._book_dir(book_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / str(attachment_id)
        # Write to a sibling temp file and rename, so a crash mid-write never
        # leaves a half-file that the metadata row points at.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def open(self, book_id: UUID, attachment_id: UUID) -> Path | None:
        path = self.path_for(book_id, attachment_id)
        return path if path.is_file() else None

    def delete(self, book_id: UUID, attachment_id: UUID) -> None:
        self.path_for(book_id, attachment_id).unlink(missing_ok=True)

    def delete_book(self, book_id: UUID) -> None:
        shutil.rmtree(self._book_dir(book_id), ignore_errors=True)

"""Receipts, invoices and scans pinned to a transaction.

Attachments are a server-side-only feature: the bytes live on the filesystem
(:mod:`pymoney.api.blobs`), the link and metadata in the ``attachments`` table
(:mod:`pymoney.store.attachments`), and neither is synced or in the change log.
A client -- web or desktop -- uploads to the server and downloads on demand; a
desktop working offline simply cannot add one until it reconnects.

The upload is hardened the same way the KMyMoney import is: bounded chunked
read, a size limit from settings, a content-type allow-list of common photo and
scan formats plus PDF, and a magic-byte sniff so a mislabelled ``.exe`` is
refused. The server stores the bytes exactly as received and does no image
processing.

Every route needs a grant on the book -- ``viewer`` to see or download,
``editor`` to add or remove -- so nothing here is reachable without
authentication.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import Engine

from ...store import attachments as store
from ...store import write_transaction
from ..blobs import BlobStore
from ..deps import (
    CurrentUser,
    ReadableBook,
    WritableBook,
    get_blobs,
    get_engine,
    get_settings_dep,
)
from ..schemas import AttachmentOut
from ..settings import Settings
from ..uploads import read_limited
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["attachments"])

UNPROCESSABLE = 422

# Leading bytes that vouch for a format, so a mislabelled upload is caught.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _safe_name(raw: str | None) -> str:
    """A display filename with every path component stripped."""
    name = (raw or "attachment").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in '"')[:255]
    return name or "attachment"


def _sniff(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for prefix, kind in _MAGIC:
        if data.startswith(prefix):
            return kind
    return None


def _check_type(declared: str | None, data: bytes, allowed: list[str]) -> str:
    sniffed = _sniff(data)
    declared = (declared or "").split(";", 1)[0].strip().lower() or None
    content_type = declared or sniffed
    if content_type not in allowed:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=(
                "attachments must be an image (JPEG, PNG, GIF, WebP, TIFF) or a "
                f"PDF; got {content_type or 'an unknown type'}"
            ),
        )
    # A recognised magic number that contradicts the label is a lie worth
    # refusing; an unrecognised one (HEIC has no fixed prefix) is allowed
    # through on the declared type.
    if sniffed is not None and declared is not None and sniffed != declared:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"the file is {sniffed}, not the {declared} it claims to be",
        )
    return content_type


@router.get(
    "/transactions/{transaction_id}/attachments",
    response_model=list[AttachmentOut],
    summary="List a transaction's attachments",
)
def list_attachments(
    transaction_id: UUID,
    book: ReadableBook,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
) -> list[AttachmentOut]:
    set_revision(response, book)
    book.transaction(transaction_id)  # 404 if it is not this book's
    with engine.connect() as conn:
        rows = store.attachments_for_transaction(conn, book.id, transaction_id)
    return [AttachmentOut.of(r) for r in rows]


@router.post(
    "/transactions/{transaction_id}/attachments",
    response_model=AttachmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a receipt or invoice to a transaction",
    responses={413: {"description": "The file is larger than the server accepts."}},
)
def upload_attachment(
    transaction_id: UUID,
    book: WritableBook,
    response: Response,
    actor: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
    blobs: Annotated[BlobStore, Depends(get_blobs)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File(description="An image or a PDF.")],
) -> AttachmentOut:
    book.transaction(transaction_id)
    limit = settings.max_attachment_mb * 1024 * 1024
    data = read_limited(file.file, limit)
    if not data:
        raise HTTPException(status_code=UNPROCESSABLE, detail="the file is empty")
    content_type = _check_type(file.content_type, data, settings.attachment_content_types)
    import hashlib

    from ...core.ids import new_id

    attachment_id = new_id()
    digest = hashlib.sha256(data).hexdigest()
    blobs.write(book.id, attachment_id, data)
    try:
        with write_transaction(engine) as conn:
            row = store.add_attachment(
                conn,
                book_id=book.id,
                transaction_id=transaction_id,
                filename=_safe_name(file.filename),
                content_type=content_type,
                byte_size=len(data),
                sha256=digest,
                uploaded_by=actor.subject,
                attachment_id=attachment_id,
            )
    except BaseException:
        blobs.delete(book.id, attachment_id)
        raise
    set_revision(response, book)
    return AttachmentOut.of(row)


@router.get(
    "/attachments/{attachment_id}",
    summary="Download an attachment's bytes",
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def download_attachment(
    attachment_id: UUID,
    book: ReadableBook,
    engine: Annotated[Engine, Depends(get_engine)],
    blobs: Annotated[BlobStore, Depends(get_blobs)],
) -> FileResponse:
    with engine.connect() as conn:
        row = store.get_attachment(conn, book.id, attachment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no attachment")
    path = blobs.open(book.id, attachment_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="the attachment's file is missing from storage",
        )
    # inline, with the original name, so a browser shows the receipt rather
    # than forcing a download but a right-click "save as" still gets it right.
    return FileResponse(
        path,
        media_type=row.content_type,
        filename=row.filename,
        content_disposition_type="inline",
    )


@router.delete(
    "/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an attachment",
)
def delete_attachment(
    attachment_id: UUID,
    book: WritableBook,
    engine: Annotated[Engine, Depends(get_engine)],
    blobs: Annotated[BlobStore, Depends(get_blobs)],
) -> None:
    with engine.connect() as conn:
        row = store.get_attachment(conn, book.id, attachment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no attachment")
    with write_transaction(engine) as conn:
        store.delete_attachment(conn, book.id, attachment_id)
    blobs.delete(book.id, attachment_id)


__all__ = ["router"]

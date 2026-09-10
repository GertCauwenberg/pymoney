"""Importing a KMyMoney file into a brand-new book, server side.

The command-line importer exists for an administrator with a shell. A user has
a browser and a ``.kmy`` file, so: upload it, the server makes a book, imports
into it and grants the uploader ``owner`` -- all in one request, so there is
never a book nobody can see. If the import fails the book goes too.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from ...core.errors import PyMoneyError
from ...importers.kmymoney import ImportOptions, import_kmymoney
from ...importers.kmymoney.parser import KmyFormatError
from ..deps import CurrentUser, get_registry, get_settings_dep
from ..registry import BookRegistry
from ..schemas import BookOut, ImportOut, ImportProblemOut
from ..settings import Settings
from ..uploads import stream_to_file

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422

router = APIRouter(tags=["books"])


@router.post(
    "/books/import/kmymoney",
    response_model=ImportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a book from an uploaded KMyMoney file",
    responses={413: {"description": "The file is larger than the server accepts."}},
)
def import_kmymoney_file(
    actor: CurrentUser,
    registry: Annotated[BookRegistry, Depends(get_registry)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File(description="A .kmy file, gzipped or plain XML.")],
    name: Annotated[
        str, Form(min_length=1, max_length=255, description="Name for the new book.")
    ],
    rounding_adjustments: Annotated[bool, Form()] = True,
    skip_prices: Annotated[bool, Form()] = False,
) -> ImportOut:
    """The uploader becomes the book's owner. Every balance is verified against
    what KMyMoney displayed; the report says what was approximated or skipped."""
    limit = settings.max_upload_mb * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="pymoney-import-") as scratch:
        path = Path(scratch) / (Path(file.filename or "upload.kmy").name or "upload.kmy")
        # The same chunked, Content-Length-ignoring size guard the attachment
        # endpoint uses; 413 the moment the stream passes the limit.
        stream_to_file(file.file, path, limit)

        # The uploader owns the book from the moment it exists (the grant is
        # part of BookRegistry.create), so even if the cleanup below could not
        # run -- the process killed mid-import -- what is left is a book its
        # owner can see and delete, never one nobody can reach.
        book = registry.create(
            name=name.strip(),
            base_currency=None,
            actor=actor.subject,
            owner=actor.subject,
        )
        try:
            with registry.open(book.id) as opened:
                report = import_kmymoney(
                    path,
                    opened,
                    options=ImportOptions(
                        adjust_rounding=rounding_adjustments,
                        skip_prices=skip_prices,
                        # The upload limit bounds the *compressed* file; this
                        # bounds what it may inflate to. Real ledgers gzip
                        # about 10:1; forty times the limit is generous, and a
                        # bomb is not a ledger.
                        max_xml_bytes=limit * 40,
                    ),
                )
        except BaseException as exc:
            # Whatever went wrong -- a bad file, a bug, a disk full, the client
            # gone -- a failed import leaves no book behind.
            registry.discard(book.id)
            if isinstance(exc, KmyFormatError | PyMoneyError | ValueError):
                raise HTTPException(
                    status_code=UNPROCESSABLE,
                    detail=f"the file could not be imported: {exc}",
                ) from exc
            raise

    with registry.open(book.id) as opened:
        out_book = BookOut.of(opened.settings, opened.revision, "owner")
    return ImportOut(
        book=out_book,
        ok=report.ok,
        imported=dict(report.imported),
        skipped=dict(report.skipped),
        problems=[
            ImportProblemOut(
                level=p.level.name,
                category=p.category,
                message=p.message,
                source_id=p.source_id,
            )
            for p in report.problems
        ],
        adjustments=[str(a) for a in report.adjustments],
        summary=report.summary(),
    )


__all__ = ["router"]

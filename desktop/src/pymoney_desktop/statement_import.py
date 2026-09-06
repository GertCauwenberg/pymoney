"""Importing a bank statement (CSV): the five calls, once.

Both the online and the offline client delegate the whole flow to the server.
The duplicate and transfer detection needs the book as the server sees it --
not a replica that may be a write or two behind -- and a statement is small,
so there is nothing to gain from doing it locally and a cent to lose. So,
like creating a book, importing a statement needs the connection; offline, it
surfaces the same :class:`~pymoney_desktop.client.OfflineError` as any other
write that cannot reach the server.

These are module functions rather than a mixin because the two clients share
nothing but a :class:`~pymoney_desktop.transport.Transport`, and a function
that takes one is the smaller thing to depend on.
"""

from __future__ import annotations

import json
from uuid import UUID

from pymoney.api.schemas import (
    ColumnMappingModel,
    CsvInspectOut,
    CsvPreviewOut,
    ImportDecisionIn,
    StatementImportOut,
)

from .transport import Transport, raise_for_problem

__all__ = [
    "commit_csv",
    "import_mapping",
    "inspect_csv",
    "preview_csv",
    "save_import_mapping",
]

_CSV_PART = "text/csv"


def _json(transport: Transport, method: str, path: str, body: object = None) -> object:
    status, _headers, payload = transport(method, path, body, {})
    raise_for_problem(status, payload)
    return payload


def _upload(
    transport: Transport, path: str, fields: dict[str, str], data: bytes
) -> object:
    status, _headers, payload = transport(
        "POST",
        path,
        fields,
        {},
        files={"file": ("statement.csv", data, _CSV_PART)},
    )
    raise_for_problem(status, payload)
    return payload


def inspect_csv(transport: Transport, book_id: UUID, data: bytes) -> CsvInspectOut:
    """Read the upload: its columns, a few rows, and a mapping to start from."""
    return CsvInspectOut.model_validate(
        _upload(transport, f"/books/{book_id}/imports/csv/inspect", {}, data)
    )


def preview_csv(
    transport: Transport,
    book_id: UUID,
    account_id: UUID,
    data: bytes,
    mapping: ColumnMappingModel,
) -> CsvPreviewOut:
    """Classify every row against the book. Writes nothing."""
    return CsvPreviewOut.model_validate(
        _upload(
            transport,
            f"/books/{book_id}/accounts/{account_id}/imports/csv/preview",
            {"mapping": mapping.model_dump_json()},
            data,
        )
    )


def commit_csv(
    transport: Transport,
    book_id: UUID,
    account_id: UUID,
    data: bytes,
    mapping: ColumnMappingModel,
    decisions: list[ImportDecisionIn],
    *,
    uncategorised_account_id: UUID | None = None,
) -> StatementImportOut:
    """Post the reviewed statement; the server remembers the mapping."""
    fields = {
        "mapping": mapping.model_dump_json(),
        "decisions": json.dumps([d.model_dump(mode="json") for d in decisions]),
    }
    if uncategorised_account_id is not None:
        fields["uncategorised_account_id"] = str(uncategorised_account_id)
    return StatementImportOut.model_validate(
        _upload(
            transport,
            f"/books/{book_id}/accounts/{account_id}/imports/csv/commit",
            fields,
            data,
        )
    )


def import_mapping(
    transport: Transport, book_id: UUID, account_id: UUID
) -> ColumnMappingModel | None:
    """The mapping last used for this account, or ``None`` if never imported to."""
    payload = _json(
        transport, "GET", f"/books/{book_id}/accounts/{account_id}/import-mapping"
    )
    return None if payload is None else ColumnMappingModel.model_validate(payload)


def save_import_mapping(
    transport: Transport,
    book_id: UUID,
    account_id: UUID,
    mapping: ColumnMappingModel,
) -> ColumnMappingModel:
    """Store a mapping without importing in the same breath."""
    return ColumnMappingModel.model_validate(
        _json(
            transport,
            "PUT",
            f"/books/{book_id}/accounts/{account_id}/import-mapping",
            mapping.model_dump(mode="json"),
        )
    )

"""Importing a bank statement (CSV) into an account of an existing book.

The KMyMoney endpoint (:mod:`pymoney.api.routers.imports`) makes a whole new
book once. These are the other half: the weekly download, reconciled against a
chart of accounts and a list of payees that already exist.

The flow is three stateless calls, because a half-finished import is not a
state worth keeping on the server:

``POST .../imports/csv/inspect``
    File in. Back: the columns, a few sample rows, and a *suggested* mapping --
    enough to render the column-mapping screen.
``POST .../accounts/{id}/imports/csv/preview``
    File plus the confirmed mapping. Back: one proposal per row (payee and
    category guessed, duplicates and transfers flagged), for the review screen.
``POST .../accounts/{id}/imports/csv/commit``
    File, mapping and the reviewer's decisions. The rows are re-derived and
    re-classified under the book's lock -- the far side of a transfer may have
    posted since the preview -- then written in one ``book.bulk()``. The
    mapping is saved as this account's, for next week.

The file is uploaded each time. That is deliberate: the server, not the client,
is the authority on what is in it, so a decision can only steer the soft fields
(payee, category, skip). The bank's own numbers are not the reviewer's to edit
here.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine

from ...core.account import Account
from ...importers.statement import (
    ColumnMapping,
    StatementFormatError,
    entries_from_rows,
    import_statement,
    propose,
    read_csv,
    suggest_mapping,
)
from ...store import write_transaction
from ...store.import_mappings import get_mapping, save_mapping
from ..deps import WritableBook, get_engine, get_settings_dep
from ..schemas import (
    ColumnMappingModel,
    CsvInspectOut,
    CsvPreviewOut,
    ImportDecisionIn,
    ImportProposalOut,
    StatementImportOut,
)
from ..settings import Settings
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}", tags=["imports"])

# Starlette renamed this status constant; 422 is the number either way.
UNPROCESSABLE = 422
_CHUNK = 256 * 1024
_DECISIONS = TypeAdapter(list[ImportDecisionIn])


async def _read_upload(file: UploadFile, settings: Settings) -> bytes:
    """The file, in memory, bounded by ``PYMONEY_MAX_UPLOAD_MB``.

    A statement is kilobytes; holding it in memory is simpler than a temp file
    and there is nothing to clean up if the request is abandoned mid-parse.
    """
    limit = settings.max_upload_mb * 1024 * 1024
    buffer = bytearray()
    while chunk := await file.read(_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"the file is larger than {settings.max_upload_mb} MB",
            )
    if not buffer:
        raise HTTPException(
            status_code=UNPROCESSABLE, detail="the uploaded file is empty"
        )
    return bytes(buffer)


def _mapping(form_value: str) -> ColumnMappingModel:
    try:
        return ColumnMappingModel.model_validate_json(form_value)
    except ValidationError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"the 'mapping' field is not a valid column mapping: {exc}",
        ) from exc


def _decisions(form_value: str | None) -> list[ImportDecisionIn]:
    if not form_value or not form_value.strip():
        return []
    try:
        return _DECISIONS.validate_json(form_value)
    except ValidationError as exc:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=f"the 'decisions' field is not a valid decision list: {exc}",
        ) from exc


def _account(book: WritableBook, account_id: UUID) -> Account:
    account = book.account(account_id)  # 404 if it is not in this book
    if account.placeholder:
        raise HTTPException(
            status_code=UNPROCESSABLE,
            detail=(
                f"{book.path(account)!r} is a placeholder and holds no "
                f"transactions; import into one of its children"
            ),
        )
    return account


def _parse(data: bytes, mapping: ColumnMappingModel, account: Account):
    domain = mapping.to_domain()
    read = read_csv(
        data,
        encoding=domain.encoding,
        delimiter=domain.delimiter,
        has_header=domain.has_header,
        skip_rows=domain.skip_rows,
    )
    return entries_from_rows(
        read.rows, domain, account_id=account.id, columns=read.columns
    )


# ==========================================================================
# 1. look at the file
# ==========================================================================
@router.post(
    "/imports/csv/inspect",
    response_model=CsvInspectOut,
    summary="Read an uploaded CSV and suggest a column mapping",
    responses={413: {"description": "The file is larger than the server accepts."}},
)
async def inspect_csv(
    book: WritableBook,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File(description="A bank statement as CSV.")],
) -> CsvInspectOut:
    """Sniffs the delimiter, the header and any preamble, and guesses which
    column is the date, the amount and the description. Every guess is the
    client's to override before a preview."""
    data = await _read_upload(file, settings)
    try:
        read = read_csv(data)
        suggested = suggest_mapping(read)
    except StatementFormatError as exc:
        raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
    return CsvInspectOut(
        columns=list(read.columns),
        sample_rows=[dict(row) for row in read.truncated_sample],
        row_count=read.row_count,
        encoding=read.encoding,
        delimiter=read.delimiter,
        has_header=read.has_header,
        skip_rows=read.skip_rows,
        suggested_mapping=ColumnMappingModel.of(suggested),
    )


# ==========================================================================
# 2. review what it would do
# ==========================================================================
@router.post(
    "/accounts/{account_id}/imports/csv/preview",
    response_model=CsvPreviewOut,
    summary="Classify a statement's rows against the book, without posting",
    responses={413: {"description": "The file is larger than the server accepts."}},
)
async def preview_csv(
    account_id: UUID,
    book: WritableBook,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File(description="The same statement as CSV.")],
    mapping: Annotated[str, Form(description="A column mapping, as JSON.")],
) -> CsvPreviewOut:
    """Each row comes back as ``new``, ``duplicate`` or ``transfer``, with a
    suggested payee and category. Nothing is written; this is the input to the
    review screen."""
    account = _account(book, account_id)
    model = _mapping(mapping)
    data = await _read_upload(file, settings)
    try:
        entries = _parse(data, model, account)
    except StatementFormatError as exc:
        raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc
    proposals = propose(book, account, entries)
    set_revision(response, book)
    return CsvPreviewOut(
        account_id=account.id,
        mapping=model,
        proposals=[
            ImportProposalOut.of(proposal, account.commodity) for proposal in proposals
        ],
    )


# ==========================================================================
# 3. post it
# ==========================================================================
@router.post(
    "/accounts/{account_id}/imports/csv/commit",
    response_model=StatementImportOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post a reviewed statement into the account",
    responses={413: {"description": "The file is larger than the server accepts."}},
)
async def commit_csv(
    account_id: UUID,
    book: WritableBook,
    response: Response,
    engine: Annotated[Engine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: Annotated[UploadFile, File(description="The reviewed statement as CSV.")],
    mapping: Annotated[str, Form(description="The column mapping, as JSON.")],
    decisions: Annotated[
        str | None,
        Form(
            description=(
                "A JSON list of per-row decisions; a row with none takes the "
                "proposal's default."
            )
        ),
    ] = None,
    uncategorised_account_id: Annotated[
        UUID | None,
        Form(
            description=(
                "Where a posted row with no chosen category balances to. "
                "Required if any such row is to be posted."
            )
        ),
    ] = None,
) -> StatementImportOut:
    """Re-derives the rows from the file, re-checks for duplicates and
    transfers under the book's lock (the far side of a transfer may have
    arrived since the preview), applies the decisions, and writes the lot in
    one unit of work. A malformed row is reported, not fatal. The mapping is
    saved as this account's."""
    account = _account(book, account_id)
    model = _mapping(mapping)
    parsed_decisions = _decisions(decisions)
    data = await _read_upload(file, settings)
    try:
        entries = _parse(data, model, account)
    except StatementFormatError as exc:
        raise HTTPException(status_code=UNPROCESSABLE, detail=str(exc)) from exc

    report = import_statement(
        book,
        account,
        entries,
        {d.row_key: d.to_domain() for d in parsed_decisions},
        uncategorised_account_id=uncategorised_account_id,
    )
    # Remember the mapping only once the import it produced has committed.
    with write_transaction(engine) as conn:
        save_mapping(conn, book.id, account.id, model.to_domain().to_dict())
    set_revision(response, book)
    return StatementImportOut.of(report)


# ==========================================================================
# the remembered mapping
# ==========================================================================
@router.get(
    "/accounts/{account_id}/import-mapping",
    response_model=ColumnMappingModel | None,
    summary="The CSV column mapping last used for this account",
)
def get_import_mapping(
    account_id: UUID,
    book: WritableBook,
    engine: Annotated[Engine, Depends(get_engine)],
) -> ColumnMappingModel | None:
    """``null`` if no statement has ever been imported into the account."""
    book.account(account_id)
    with engine.connect() as conn:
        stored = get_mapping(conn, book.id, account_id)
    if stored is None:
        return None
    try:
        return ColumnMappingModel.of(ColumnMapping.from_dict(stored))
    except StatementFormatError:
        # Saved under an older shape that no longer validates (a required
        # field since renamed). A fresh inspect rebuilds it; nothing a user
        # typed in the ledger is lost.
        return None


@router.put(
    "/accounts/{account_id}/import-mapping",
    response_model=ColumnMappingModel,
    summary="Save a CSV column mapping for this account",
)
def put_import_mapping(
    account_id: UUID,
    body: ColumnMappingModel,
    book: WritableBook,
    engine: Annotated[Engine, Depends(get_engine)],
) -> ColumnMappingModel:
    """For a mapping a user built or fixed without importing in the same
    breath; a successful commit saves the mapping it used anyway."""
    book.account(account_id)
    domain = body.to_domain()  # its own __post_init__ is the validation
    with write_transaction(engine) as conn:
        save_mapping(conn, book.id, account_id, domain.to_dict())
    return ColumnMappingModel.of(domain)


__all__ = ["router"]

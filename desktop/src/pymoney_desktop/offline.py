"""The offline-capable client: a local replica, an outbox, and replay.

Three tables of its own sit beside the ledger schema in one SQLite file:

``desktop_kv``
    The session token, the user, the list of books -- what a cold start needs
    to show something before the network answers.
``desktop_sync``
    Per book, the server revision the replica has applied (its watermark).
``desktop_outbox``
    Writes waiting for the server, in order, each with the idempotency key it
    will be sent with, so a replay interrupted by a dropped connection cannot
    apply a command twice.

**Writes** run against the local book first. That is not a shortcut: the local
book is the same :class:`~pymoney.core.book.Book` over the same
:class:`~pymoney.store.SqlStore` the server runs, so the same invariants are
checked and the same errors come back -- an unbalanced entry is refused in the
living room, not on the train back. Ids are chosen here (UUIDv7), so the
server's row and the local one are one row when the command is replayed.

**Replay** sends the outbox in order. A 2xx removes the entry. A 4xx that
judges *the command itself* (unbalanced, conflict, gone) is a *rejection*: the
server, which is authoritative, disagrees with what the replica accepted --
typically because someone else changed the book in between. The entry is
removed, recorded for the user to see, and the replica is rebuilt from the
server, after which the remaining outbox entries are applied to it again so the
screen keeps showing what is still pending. Nothing is merged, ever. A status
that says nothing about the command -- no connection, a 5xx, 429, 403, or a 401
from an expired session -- stops the replay and the outbox stays: work queued
on a train must survive the session dying before the train arrives. A 401
additionally forgets the stored credentials, so the UI asks for a login and the
queue drains on the first sync after it.

**Pull** follows every replay: ``GET /changes`` from the watermark, applied
batch by batch. A pull cannot disturb a pending local write: the server knows
nothing of it, so no batch touches its rows.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pymoney.api import schemas as wire
from pymoney.api.errors import status_for
from pymoney.api.routers._shared import cursor_key, decode_cursor
from pymoney.api.schemas import (
    AccountBalanceOut,
    AccountIn,
    AccountOut,
    AmountOut,
    BalanceSheetOut,
    BookIn,
    BookOut,
    CommodityOut,
    HoldingsOut,
    IncomeStatementOut,
    NetWorthSeriesOut,
    PayeeIn,
    PayeeOut,
    PeriodTableOut,
    RegisterPage,
    ReportInterval,
    SessionOut,
    SyncPageOut,
    TransactionIn,
    TransactionOut,
    TransactionSearchOut,
    TransactionUpdateIn,
    UserOut,
)
from pymoney.api.views import (
    account_balance,
    balance_sheet_out,
    holdings_out,
    income_statement_out,
    net_worth_series_out,
    period_table_out,
    register_page,
    search_transactions,
)
from pymoney.core import commands as cmd
from pymoney.core.book import Book
from pymoney.core.clock import Clock, SystemClock
from pymoney.core.commands import UNSET
from pymoney.core.errors import PyMoneyError
from pymoney.core.ids import new_id
from pymoney.core.posting import LegSpec
from pymoney.core.price import ValuationPolicy
from pymoney.core.reports import account_tree
from pymoney.core.rounding import to_fraction
from pymoney.core.store import TransactionSearch
from pymoney.store import create_engine, create_schema, open_book, schema
from pymoney.store.types import Uuid
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, delete, select
from sqlalchemy.engine import Engine

from .client import (
    ApiError,
    ConnectionStatus,
    LedgerClient,
    OfflineError,
    QueuedCommand,
    Rejection,
    ReportOptions,
    SearchCriteria,
)
from .replica import apply_batch
from .transport import Transport, raise_for_problem

__all__ = ["OfflineClient"]

local = MetaData()
kv = Table(
    "desktop_kv",
    local,
    Column("key", String(64), primary_key=True),
    Column("value", Text, nullable=False),
)
sync_state = Table(
    "desktop_sync",
    local,
    Column("book_id", Uuid, primary_key=True),
    Column("watermark", Integer, nullable=False),
)
outbox = Table(
    "desktop_outbox",
    local,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("book_id", Uuid, nullable=False),
    Column("op", String(32), nullable=False),
    Column("method", String(8), nullable=False),
    Column("path", Text, nullable=False),
    Column("body", Text, nullable=True),
    Column("idempotency_key", String(64), nullable=False),
    Column("queued_at", String(40), nullable=False),
    Column("summary", Text, nullable=False),
)


_TRY_AGAIN_LATER = frozenset({403, 408, 425, 429})
"""Statuses that, like a 5xx, do not judge the queued command itself.

403: the caller's grant, which may be mid-change -- dropping the work because a
role was revoked for an afternoon would destroy it for good. 408/425/429: the
server asked us to come back later. In every case the entry stays queued and
``status()`` says why nothing is moving.
"""


@dataclass(frozen=True, slots=True)
class _Op:
    """A local write: how to apply it to the replica and how to send it."""

    name: str
    method: str
    path: str
    body: dict | None
    summary: str


class OfflineClient(LedgerClient):
    def __init__(
        self,
        transport: Transport,
        path: str | Path,
        *,
        clock: Clock | None = None,
        token_sink: Callable[[str | None], None] | None = None,
    ) -> None:
        self._transport = transport
        self._clock: Clock = clock or SystemClock()
        self._token_sink = token_sink or (
            (lambda token: setattr(transport, "token", token))
            if hasattr(transport, "token")
            else (lambda token: None)
        )
        self._engine: Engine = create_engine(f"sqlite+pysqlite:///{Path(path)}")
        create_schema(self._engine)
        local.create_all(self._engine)
        self._books: dict[UUID, Book] = {}
        self._online = True
        self._last_error: str | None = None
        self._last_sync: datetime | None = None
        self._rejections: list[Rejection] = []
        token = self._kv_get("token")
        if token:
            self._token_sink(token)

    # ======================================================================
    # local state
    # ======================================================================
    def _kv_get(self, key: str) -> str | None:
        with self._engine.connect() as conn:
            return conn.execute(
                select(kv.c.value).where(kv.c.key == key)
            ).scalar_one_or_none()

    def _kv_set(self, key: str, value: str | None) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(kv).where(kv.c.key == key))
            if value is not None:
                conn.execute(kv.insert().values(key=key, value=value))

    def _watermark(self, book_id: UUID) -> int:
        with self._engine.connect() as conn:
            found = conn.execute(
                select(sync_state.c.watermark).where(sync_state.c.book_id == book_id)
            ).scalar_one_or_none()
        return int(found or 0)

    def _set_watermark(self, book_id: UUID, watermark: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(sync_state).where(sync_state.c.book_id == book_id))
            conn.execute(sync_state.insert().values(book_id=book_id, watermark=watermark))

    def _book(self, book_id: UUID) -> Book:
        book = self._books.get(book_id)
        if book is None:
            book = self._books[book_id] = open_book(
                self._engine, book_id=book_id, clock=self._clock
            )
        return book

    def _reopen(self, book_id: UUID) -> Book:
        """After a pull changed the book row, a fresh Book sees the new name and base."""
        old = self._books.pop(book_id, None)
        if old is not None:
            old.close()
        return self._book(book_id)

    def _wipe(self, book_id: UUID) -> None:
        """Forget everything local about a book, so it can be pulled afresh."""
        old = self._books.pop(book_id, None)
        if old is not None:
            old.close()
        with self._engine.begin() as conn:
            # Children before parents: splits before transactions, transactions
            # before the payees and accounts they reference, and so on.
            for table in (
                schema.splits,
                schema.transactions,
                schema.prices,
                schema.payees,
                schema.accounts,
                schema.commodities,
                schema.changes,
                schema.command_results,
            ):
                conn.execute(delete(table).where(table.c.book_id == book_id))
            conn.execute(delete(schema.books).where(schema.books.c.id == book_id))
            conn.execute(delete(sync_state).where(sync_state.c.book_id == book_id))

    # ======================================================================
    # the network
    # ======================================================================
    def _call(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        try:
            status, _headers, payload = self._transport(method, path, body, headers or {})
        except OfflineError as exc:
            self._online, self._last_error = False, str(exc)
            raise
        self._online = True
        raise_for_problem(status, payload)
        return payload

    # ======================================================================
    # session
    # ======================================================================
    def login(self, email: str, password: str) -> UserOut:
        session = SessionOut.model_validate(
            self._call("POST", "/auth/login", {"email": email, "password": password})
        )
        self._token_sink(session.token)
        self._kv_set("token", session.token)
        self._kv_set("user", session.user.model_dump_json())
        self._last_error = None
        return session.user

    def logout(self) -> None:
        """Forget the session. The local replica and any pending writes stay:
        logging back in as the same person resumes where they left off."""
        try:
            if self._kv_get("token"):
                self._call("POST", "/auth/logout")
        except (ApiError, OfflineError):
            pass
        self._token_sink(None)
        self._kv_set("token", None)
        self._kv_set("user", None)

    def me(self) -> UserOut | None:
        cached = self._kv_get("user")
        if not self._kv_get("token"):
            return None
        try:
            user = UserOut.model_validate(self._call("GET", "/auth/me"))
        except OfflineError:
            return UserOut.model_validate_json(cached) if cached else None
        except ApiError as exc:
            if exc.status == 401:
                self._kv_set("token", None)
                self._kv_set("user", None)
                return None
            raise
        self._kv_set("user", user.model_dump_json())
        return user

    def set_locale(self, locale: str) -> UserOut | None:
        cached = self._kv_get("user")
        if cached:
            user = UserOut.model_validate_json(cached).model_copy(
                update={"locale": locale}
            )
            self._kv_set("user", user.model_dump_json())
        try:
            user = UserOut.model_validate(
                self._call("PATCH", "/auth/me", {"locale": locale})
            )
        except OfflineError:
            return None
        self._kv_set("user", user.model_dump_json())
        return user

    # ======================================================================
    # books
    # ======================================================================
    def books(self) -> list[BookOut]:
        try:
            found = [BookOut.model_validate(b) for b in self._call("GET", "/books")]
        except OfflineError:
            cached = self._kv_get("books")
            if cached is None:
                raise
            return [BookOut.model_validate(b) for b in json.loads(cached)]
        self._kv_set("books", json.dumps([b.model_dump(mode="json") for b in found]))
        return found

    def create_book(self, body: BookIn) -> BookOut:
        """Creating a book needs the server: it is where grants live."""
        book = BookOut.model_validate(
            self._call("POST", "/books", body.model_dump(mode="json"))
        )
        self.books()
        return book

    # ======================================================================
    # reads: from the replica
    # ======================================================================
    def _ready(self, book_id: UUID) -> Book:
        """The local book, pulled first if it has never been."""
        if self._watermark(book_id) == 0 and book_id not in self._books:
            try:
                self._pull(book_id)
            except OfflineError as exc:
                raise OfflineError(
                    f"this book has never been synced to this device: {exc}"
                ) from exc
        return self._book(book_id)

    def accounts(self, book_id: UUID) -> list[AccountOut]:
        book = self._ready(book_id)
        return [AccountOut.of(a, book.path(a.id)) for a in book.accounts()]

    def commodities(self, book_id: UUID) -> list[CommodityOut]:
        return [CommodityOut.of(c) for c in self._ready(book_id).commodities()]

    def payees(self, book_id: UUID) -> list[PayeeOut]:
        return [PayeeOut.of(p) for p in self._ready(book_id).payees()]

    def balance(
        self, book_id: UUID, account_id: UUID, *, as_of: date | None = None
    ) -> AccountBalanceOut:
        return self._guarded(
            lambda: account_balance(self._ready(book_id), account_id, as_of=as_of)
        )

    def _policy(self, book: Book, options: ReportOptions | None) -> ValuationPolicy:
        options = options or ReportOptions()
        target = (
            book.commodity(options.target_id) if options.target_id else book.base_currency
        )
        return ValuationPolicy(
            target=target,
            as_of=options.as_of or self._clock.today(),
            knowledge_time=options.known_before,
        )

    def balance_sheet(
        self, book_id: UUID, options: ReportOptions | None = None
    ) -> BalanceSheetOut:
        book = self._ready(book_id)
        options = options or ReportOptions()
        return self._guarded(
            lambda: balance_sheet_out(
                book,
                self._policy(book, options),
                include_closed=options.include_closed,
                strict=options.strict,
            )
        )

    def income_statement(
        self, book_id: UUID, *, start: date, options: ReportOptions | None = None
    ) -> IncomeStatementOut:
        book = self._ready(book_id)
        options = options or ReportOptions()
        return self._guarded(
            lambda: income_statement_out(
                book,
                self._policy(book, options),
                start=start,
                include_closed=options.include_closed,
                strict=options.strict,
            )
        )

    def net_worth(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> NetWorthSeriesOut:
        book = self._ready(book_id)
        options = options or ReportOptions()
        return self._guarded(
            lambda: net_worth_series_out(
                book,
                self._policy(book, options),
                start=start,
                interval=interval,
                strict=options.strict,
            )
        )

    def period_table(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> PeriodTableOut:
        book = self._ready(book_id)
        options = options or ReportOptions()
        return self._guarded(
            lambda: period_table_out(
                book,
                self._policy(book, options),
                start=start,
                interval=interval,
                include_closed=options.include_closed,
                strict=options.strict,
            )
        )

    def holdings(
        self,
        book_id: UUID,
        options: ReportOptions | None = None,
        *,
        include_empty: bool = False,
    ) -> HoldingsOut:
        book = self._ready(book_id)
        options = options or ReportOptions()
        return self._guarded(
            lambda: holdings_out(
                book,
                self._policy(book, options),
                include_empty=include_empty,
                strict=options.strict,
            )
        )

    def account_balances(
        self, book_id: UUID, *, as_of: date | None = None
    ) -> dict[UUID, AmountOut]:
        book = self._ready(book_id)
        policy = book.default_policy(as_of)

        def read() -> dict[UUID, AmountOut]:
            rows = account_tree(
                book, policy, roots=book.roots(), include_closed=True, strict=False
            )
            return {row.account.id: AmountOut.of(row.value) for row in rows}

        return self._guarded(read)

    def register(
        self,
        book_id: UUID,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> RegisterPage:
        book = self._ready(book_id)
        after = None
        position = decode_cursor(cursor)
        if position is not None:
            when, transaction_int, split_int = cursor_key(position)
            after = (when, UUID(int=transaction_int), UUID(int=split_int))
        return self._guarded(
            lambda: register_page(
                book,
                account_id,
                start=start,
                end=end,
                include_children=False,
                after=after,
                size=limit,
            )
        )

    def search(self, book_id: UUID, criteria: SearchCriteria) -> TransactionSearchOut:
        book = self._ready(book_id)
        amount_min = criteria.amount or criteria.amount_min
        amount_max = criteria.amount or criteria.amount_max
        query = TransactionSearch(
            text=criteria.text,
            amount_min=None if amount_min is None else to_fraction(amount_min),
            amount_max=None if amount_max is None else to_fraction(amount_max),
            payee_id=criteria.payee_id,
            start=criteria.start,
            end=criteria.end,
        )
        return self._guarded(
            lambda: search_transactions(
                book,
                query,
                account_id=criteria.account_id,
                include_children=criteria.include_children,
                limit=criteria.limit,
            )
        )

    def transaction(self, book_id: UUID, transaction_id: UUID) -> TransactionOut:
        return self._guarded(
            lambda: TransactionOut.of(self._ready(book_id).transaction(transaction_id))
        )

    @staticmethod
    def _guarded(read: Callable[[], Any]) -> Any:
        """Domain errors from the replica come out as the server would phrase them."""
        try:
            return read()
        except PyMoneyError as exc:
            raise ApiError(status_for(exc), type(exc).__name__, str(exc)) from exc

    # ======================================================================
    # writes: the replica first, then the outbox
    # ======================================================================
    def _execute(self, book: Book, command: cmd.Command) -> Any:
        try:
            return book.execute(command).entity
        except PyMoneyError as exc:
            raise ApiError(status_for(exc), type(exc).__name__, str(exc)) from exc
        except ValueError as exc:
            raise ApiError(422, "ValueError", str(exc)) from exc

    def _enqueue(self, book_id: UUID, op: _Op) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                outbox.insert().values(
                    book_id=book_id,
                    op=op.name,
                    method=op.method,
                    path=op.path,
                    body=None if op.body is None else json.dumps(op.body),
                    idempotency_key=f"desktop-{new_id()}",
                    queued_at=self._clock.now().isoformat(),
                    summary=op.summary,
                )
            )

    def _apply_local(self, book_id: UUID, op: _Op) -> Any:
        """Run one write against the replica. Used at write time and again after a rebuild."""
        book = self._ready(book_id)
        body = op.body or {}
        if op.name == "post_transaction":
            payload = TransactionIn.model_validate(body)
            return self._execute(
                book,
                cmd.PostTransaction(
                    date=payload.date,
                    legs=_legs(payload.legs),
                    currency_id=payload.currency_id,
                    memo=payload.memo,
                    payee_id=payload.payee_id,
                    number=payload.number,
                    rounding=payload.rounding,
                    imbalance_account_id=payload.imbalance_account_id,
                    imported_id=payload.imported_id,
                    id=payload.id,
                ),
            )
        if op.name == "update_transaction":
            transaction_id = UUID(op.path.rsplit("/", 1)[1])
            patch = TransactionUpdateIn.model_validate(body)
            fields = patch.model_dump(exclude_unset=True)
            return self._execute(
                book,
                cmd.UpdateTransaction(
                    transaction_id=transaction_id,
                    date=fields.get("date", UNSET),
                    legs=_legs(patch.legs) if patch.legs is not None else UNSET,
                    memo=fields.get("memo", UNSET),
                    payee_id=fields.get("payee_id", UNSET),
                    number=fields.get("number", UNSET),
                    currency_id=fields.get("currency_id", UNSET),
                    rounding=fields.get("rounding", UNSET),
                    imbalance_account_id=patch.imbalance_account_id,
                ),
            )
        if op.name == "delete_transaction":
            return self._execute(
                book,
                cmd.DeleteTransaction(transaction_id=UUID(op.path.rsplit("/", 1)[1])),
            )
        if op.name == "create_payee":
            payee = PayeeIn.model_validate(body)
            return self._execute(
                book,
                cmd.CreatePayee(
                    name=payee.name,
                    notes=payee.notes,
                    address=payee.address,
                    match_keys=tuple(payee.match_keys),
                    default_account_id=payee.default_account_id,
                    id=payee.id,
                ),
            )
        if op.name == "create_account":
            account = AccountIn.model_validate(body)
            return self._execute(
                book,
                cmd.CreateAccount(
                    name=account.name,
                    type=account.type,
                    parent_id=account.parent_id,
                    commodity_id=account.commodity_id,
                    description=account.description,
                    code=account.code,
                    placeholder=account.placeholder,
                    notes=account.notes,
                    institution=account.institution,
                    iban=account.iban,
                    id=account.id,
                ),
            )
        raise ValueError(f"unknown local operation {op.name!r}")  # pragma: no cover

    def _write(self, book_id: UUID, op: _Op) -> Any:
        entity = self._apply_local(book_id, op)
        self._enqueue(book_id, op)
        return entity

    def post_transaction(self, book_id: UUID, body: TransactionIn) -> TransactionOut:
        body = body.model_copy(update={"id": body.id or new_id()})
        entity = self._write(
            book_id,
            _Op(
                "post_transaction",
                "POST",
                f"/books/{book_id}/transactions",
                body.model_dump(mode="json"),
                f"post {body.date} {body.memo or ''}".strip(),
            ),
        )
        return TransactionOut.of(entity)

    def update_transaction(
        self, book_id: UUID, transaction_id: UUID, body: TransactionUpdateIn
    ) -> TransactionOut:
        entity = self._write(
            book_id,
            _Op(
                "update_transaction",
                "PATCH",
                f"/books/{book_id}/transactions/{transaction_id}",
                body.model_dump(mode="json", exclude_unset=True),
                f"edit transaction {transaction_id}",
            ),
        )
        return TransactionOut.of(entity)

    def delete_transaction(self, book_id: UUID, transaction_id: UUID) -> None:
        self._write(
            book_id,
            _Op(
                "delete_transaction",
                "DELETE",
                f"/books/{book_id}/transactions/{transaction_id}",
                None,
                f"delete transaction {transaction_id}",
            ),
        )

    def create_payee(self, book_id: UUID, body: PayeeIn) -> PayeeOut:
        body = body.model_copy(update={"id": body.id or new_id()})
        entity = self._write(
            book_id,
            _Op(
                "create_payee",
                "POST",
                f"/books/{book_id}/payees",
                body.model_dump(mode="json"),
                f"new payee {body.name}",
            ),
        )
        return PayeeOut.of(entity)

    def create_account(self, book_id: UUID, body: AccountIn) -> AccountOut:
        body = body.model_copy(update={"id": body.id or new_id()})
        entity = self._write(
            book_id,
            _Op(
                "create_account",
                "POST",
                f"/books/{book_id}/accounts",
                body.model_dump(mode="json"),
                f"new account {body.name}",
            ),
        )
        return AccountOut.of(entity, self._book(book_id).path(entity.id))

    # ======================================================================
    # sync
    # ======================================================================
    def queued(self, book_id: UUID | None = None) -> list[QueuedCommand]:
        query = select(outbox).order_by(outbox.c.id)
        if book_id is not None:
            query = query.where(outbox.c.book_id == book_id)
        with self._engine.connect() as conn:
            rows = conn.execute(query).all()
        return [
            QueuedCommand(
                id=r.id,
                book_id=r.book_id,
                op=r.op,
                method=r.method,
                path=r.path,
                body=None if r.body is None else json.loads(r.body),
                idempotency_key=r.idempotency_key,
                queued_at=datetime.fromisoformat(r.queued_at),
                summary=r.summary,
            )
            for r in rows
        ]

    def _replay(self) -> set[UUID]:
        """Send the outbox in order. Returns the books whose replica must be rebuilt.

        Only a status that judges *the command itself* (unbalanced, conflict,
        gone) is a rejection: the entry is removed, recorded for the user, and
        the replica rebuilt. A status about the *caller* or the *moment* -- not
        logged in, not allowed right now, throttled, server down -- says nothing
        about the command, so the replay stops and the outbox stays; discarding
        a queued write because a session expired would be silent data loss.
        """
        rebuild: set[UUID] = set()
        for entry in self.queued():
            try:
                status, _headers, payload = self._transport(
                    entry.method,
                    entry.path,
                    entry.body,
                    {"Idempotency-Key": entry.idempotency_key},
                )
            except OfflineError as exc:
                self._online, self._last_error = False, str(exc)
                break
            self._online = True
            if 200 <= status < 300:
                self._dequeue(entry.id)
                continue
            if status == 401:
                # The session is gone, not the work. Forget the credentials --
                # exactly what me() does on a 401 -- so the UI asks for a login;
                # the queue survives and drains on the first sync afterwards.
                self._token_sink(None)
                self._kv_set("token", None)
                self._kv_set("user", None)
                self._last_error = (
                    "the session has expired; log in again to send "
                    f"{len(self.queued())} pending change(s)"
                )
                break
            if status >= 500 or status in _TRY_AGAIN_LATER:
                self._last_error = f"{status} replaying {entry.summary}; will retry"
                break
            try:
                raise_for_problem(status, payload)
            except ApiError as exc:
                self._rejections.append(
                    Rejection(
                        command=entry,
                        status=exc.status,
                        message=str(exc),
                        at=self._clock.now(),
                    )
                )
            self._dequeue(entry.id)
            rebuild.add(entry.book_id)
        return rebuild

    def _dequeue(self, entry_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(outbox).where(outbox.c.id == entry_id))

    def _pull(self, book_id: UUID) -> None:
        """Bring the replica up to the server's head, one page at a time."""
        self._book(book_id)
        while True:
            since = self._watermark(book_id)
            page = SyncPageOut.model_validate(
                self._call(
                    "GET", f"/books/{book_id}/changes?since={since}&limit_rows=2000"
                )
            )
            changed_settings = False
            for batch in page.batches:
                if apply_batch(self._book(book_id).store, book_id, batch) is not None:
                    changed_settings = True
            if changed_settings:
                self._reopen(book_id)
            self._set_watermark(book_id, max(page.to_revision, since))
            if page.complete or not page.batches:
                break

    def sync(self) -> ConnectionStatus:
        """Replay what is queued, rebuild what the server refused, pull what is new."""
        if not self._kv_get("token"):
            self._last_error = "not logged in"
            return self.status()
        rebuild = self._replay()
        if not self._kv_get("token"):
            # The replay hit a 401 and forgot the session; the pull would only
            # hit it again. The queue is intact and status() says what to do.
            return self.status()
        if self._online:
            try:
                books = self.books()
                for book in books:
                    if book.id in rebuild:
                        pending = self.queued(book.id)
                        self._wipe(book_id=book.id)
                        self._pull(book.id)
                        for entry in pending:
                            try:
                                self._apply_local(
                                    book.id,
                                    _Op(
                                        entry.op,
                                        entry.method,
                                        entry.path,
                                        entry.body,
                                        entry.summary,
                                    ),
                                )
                            except ApiError as exc:
                                # It depended on what was refused: it is refused too.
                                self._rejections.append(
                                    Rejection(
                                        command=entry,
                                        status=exc.status,
                                        message=str(exc),
                                        at=self._clock.now(),
                                    )
                                )
                                self._dequeue(entry.id)
                    else:
                        self._pull(book.id)
                self._last_sync = self._clock.now()
                self._last_error = None
            except OfflineError:
                pass
            except ApiError as exc:
                # The server answered and said no -- to the *pull*, not to any
                # queued command. sync() is a background activity: it reports
                # through status(), it does not throw at whoever scheduled it.
                self._last_error = str(exc)
                if exc.status == 401:
                    self._token_sink(None)
                    self._kv_set("token", None)
                    self._kv_set("user", None)
        return self.status()

    def status(self) -> ConnectionStatus:
        with self._engine.connect() as conn:
            marks = {
                row.book_id: row.watermark for row in conn.execute(select(sync_state))
            }
        return ConnectionStatus(
            online=self._online,
            mode="offline",
            queued=len(self.queued()),
            rejections=tuple(self._rejections),
            last_sync=self._last_sync,
            last_error=self._last_error,
            watermarks=marks,
        )

    def dismiss_rejections(self) -> None:
        self._rejections.clear()

    def close(self) -> None:
        for book in self._books.values():
            book.close()
        self._books.clear()
        self._engine.dispose()


def _legs(legs: list[wire.LegIn]) -> tuple[LegSpec, ...]:
    return tuple(
        LegSpec(
            account_id=leg.account_id,
            value=None if leg.value is None else wire.to_fraction(leg.value),
            quantity=None if leg.quantity is None else wire.to_fraction(leg.quantity),
            price=None if leg.price is None else wire.to_fraction(leg.price),
            memo=leg.memo,
            payee_id=leg.payee_id,
            action=leg.action,
            number=leg.number,
            reconcile_state=leg.reconcile_state.to_domain(),
            reconcile_date=leg.reconcile_date,
        )
        for leg in legs
    )

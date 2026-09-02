"""The online client: the API, a bearer token, and a read cache that the
server's revision header keeps honest.

Every response carries ``X-PyMoney-Revision``. The cache remembers, per book,
the revision its entries were read at; a response showing a newer revision --
because this client wrote, or because someone else did -- drops that book's
entries. So the cache can never serve a state the server has moved past without
this client having seen a newer number, and it never needs a timer.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any, TypeVar
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel
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
    ReconcileStateName,
    RegisterPage,
    ReportInterval,
    SessionOut,
    TransactionIn,
    TransactionOut,
    TransactionSearchOut,
    TransactionUpdateIn,
    UserOut,
)

from .client import (
    ApiError,
    ConnectionStatus,
    LedgerClient,
    OfflineError,
    ReportOptions,
    SearchCriteria,
)
from .transport import Transport, raise_for_problem

__all__ = ["RemoteClient"]

M = TypeVar("M", bound=BaseModel)
REVISION_HEADER = "x-pymoney-revision"


def _query(params: dict[str, Any]) -> str:
    clean = {
        k: (str(v).lower() if isinstance(v, bool) else str(v))
        for k, v in params.items()
        if v is not None
    }
    return f"?{urlencode(clean)}" if clean else ""


class RemoteClient(LedgerClient):
    def __init__(
        self,
        transport: Transport,
        *,
        token_sink: Callable[[str | None], None] | None = None,
    ) -> None:
        """
        :param transport: see :mod:`pymoney_desktop.transport`.
        :param token_sink: told the bearer token at login (and None at logout),
            so a :class:`UrllibTransport` can carry it; a test transport that
            authenticates by other means needs none.
        """
        self._transport = transport
        self._token_sink = token_sink or (
            (lambda token: setattr(transport, "token", token))
            if hasattr(transport, "token")
            else (lambda token: None)
        )
        self._user: UserOut | None = None
        self._cache: dict[UUID, tuple[int, dict[str, Any]]] = {}
        self._last_error: str | None = None
        self._online = True
        self._last_sync: datetime | None = None

    # -- plumbing --------------------------------------------------------------
    def _call(
        self, method: str, path: str, body: Any = None, *, book_id: UUID | None = None
    ) -> Any:
        try:
            status, headers, payload = self._transport(method, path, body, {})
        except OfflineError as exc:
            self._online, self._last_error = False, str(exc)
            raise
        self._online, self._last_error = True, None
        self._last_sync = datetime.now(timezone.utc)
        raise_for_problem(status, payload)
        if book_id is not None:
            revision = next(
                (int(v) for k, v in headers.items() if k.lower() == REVISION_HEADER), None
            )
            if revision is not None:
                cached = self._cache.get(book_id)
                if cached is None or cached[0] != revision:
                    self._cache[book_id] = (revision, {})
        return payload

    def _cached(self, book_id: UUID, key: str, model: type[M], path: str) -> M:
        entry = self._cache.get(book_id)
        if entry is not None and key in entry[1]:
            return entry[1][key]
        payload = self._call("GET", path, book_id=book_id)
        value = model.model_validate(payload)
        # _call may have replaced the entry (new revision): write into the current one.
        self._cache.setdefault(book_id, (0, {}))[1][key] = value
        return value

    def _cached_list(self, book_id: UUID, key: str, model: type[M], path: str) -> list[M]:
        entry = self._cache.get(book_id)
        if entry is not None and key in entry[1]:
            return entry[1][key]
        payload = self._call("GET", path, book_id=book_id)
        value = [model.model_validate(item) for item in payload]
        self._cache.setdefault(book_id, (0, {}))[1][key] = value
        return value

    def _written(self, book_id: UUID) -> None:
        self._cache.pop(book_id, None)

    # -- session -----------------------------------------------------------------
    def login(self, email: str, password: str) -> UserOut:
        session = SessionOut.model_validate(
            self._call("POST", "/auth/login", {"email": email, "password": password})
        )
        self._token_sink(session.token)
        self._user = session.user
        return session.user

    def logout(self) -> None:
        try:
            if self._user is not None:
                self._call("POST", "/auth/logout")
        except (ApiError, OfflineError):
            pass
        finally:
            self._token_sink(None)
            self._user = None
            self._cache.clear()

    def me(self) -> UserOut | None:
        if self._user is None:
            try:
                self._user = UserOut.model_validate(self._call("GET", "/auth/me"))
            except ApiError as exc:
                if exc.status == 401:
                    return None
                raise
        return self._user

    def set_locale(self, locale: str) -> UserOut | None:
        self._user = UserOut.model_validate(
            self._call("PATCH", "/auth/me", {"locale": locale})
        )
        return self._user

    # -- books -------------------------------------------------------------------
    def books(self) -> list[BookOut]:
        return [BookOut.model_validate(b) for b in self._call("GET", "/books")]

    def create_book(self, body: BookIn) -> BookOut:
        return BookOut.model_validate(
            self._call("POST", "/books", body.model_dump(mode="json"))
        )

    # -- reference data ------------------------------------------------------------
    def accounts(self, book_id: UUID) -> list[AccountOut]:
        return self._cached_list(
            book_id, "accounts", AccountOut, f"/books/{book_id}/accounts"
        )

    def commodities(self, book_id: UUID) -> list[CommodityOut]:
        return self._cached_list(
            book_id, "commodities", CommodityOut, f"/books/{book_id}/commodities"
        )

    def payees(self, book_id: UUID) -> list[PayeeOut]:
        return self._cached_list(book_id, "payees", PayeeOut, f"/books/{book_id}/payees")

    def balance(
        self, book_id: UUID, account_id: UUID, *, as_of: date | None = None
    ) -> AccountBalanceOut:
        return self._cached(
            book_id,
            f"balance:{account_id}:{as_of}",
            AccountBalanceOut,
            f"/books/{book_id}/accounts/{account_id}/balance{_query({'as_of': as_of})}",
        )

    @staticmethod
    def _report_query(options: ReportOptions | None, **extra: Any) -> dict[str, Any]:
        options = options or ReportOptions()
        return {
            "as_of": options.as_of,
            "target_id": options.target_id,
            "known_before": None
            if options.known_before is None
            else options.known_before.isoformat(),
            "include_closed": options.include_closed,
            "strict": options.strict,
            **extra,
        }

    def balance_sheet(
        self, book_id: UUID, options: ReportOptions | None = None
    ) -> BalanceSheetOut:
        query = _query(self._report_query(options))
        return self._cached(
            book_id,
            f"balance-sheet:{query}",
            BalanceSheetOut,
            f"/books/{book_id}/reports/balance-sheet{query}",
        )

    def income_statement(
        self, book_id: UUID, *, start: date, options: ReportOptions | None = None
    ) -> IncomeStatementOut:
        params = self._report_query(options, start=start)
        params["end"] = params.pop("as_of")
        query = _query(params)
        return self._cached(
            book_id,
            f"income-statement:{query}",
            IncomeStatementOut,
            f"/books/{book_id}/reports/income-statement{query}",
        )

    def net_worth(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> NetWorthSeriesOut:
        params = self._report_query(options, start=start, interval=interval)
        params["end"] = params.pop("as_of")
        params.pop("include_closed")
        query = _query(params)
        return self._cached(
            book_id,
            f"net-worth:{query}",
            NetWorthSeriesOut,
            f"/books/{book_id}/reports/net-worth{query}",
        )

    def period_table(
        self,
        book_id: UUID,
        *,
        start: date,
        interval: ReportInterval = "month",
        options: ReportOptions | None = None,
    ) -> PeriodTableOut:
        params = self._report_query(options, start=start, interval=interval)
        params["end"] = params.pop("as_of")
        query = _query(params)
        return self._cached(
            book_id,
            f"period-table:{query}",
            PeriodTableOut,
            f"/books/{book_id}/reports/period-table{query}",
        )

    def holdings(
        self,
        book_id: UUID,
        options: ReportOptions | None = None,
        *,
        include_empty: bool = False,
    ) -> HoldingsOut:
        params = self._report_query(options, include_empty=include_empty)
        params.pop("include_closed")
        query = _query(params)
        return self._cached(
            book_id,
            f"holdings:{query}",
            HoldingsOut,
            f"/books/{book_id}/reports/holdings{query}",
        )

    def account_balances(
        self, book_id: UUID, *, as_of: date | None = None
    ) -> dict[UUID, AmountOut]:
        sheet = self.balance_sheet(
            book_id, ReportOptions(as_of=as_of, include_closed=True)
        )
        statement = self._cached(
            book_id,
            f"income-statement:{as_of}",
            IncomeStatementOut,
            f"/books/{book_id}/reports/income-statement"
            + _query(
                {
                    "start": date(1900, 1, 1),
                    "end": as_of,
                    "include_closed": True,
                    "strict": False,
                }
            ),
        )
        return {row.account_id: row.value for row in [*sheet.rows, *statement.rows]}

    # -- ledger ----------------------------------------------------------------------
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
        query = _query({"start": start, "end": end, "cursor": cursor, "limit": limit})
        return self._cached(
            book_id,
            f"register:{account_id}:{query}",
            RegisterPage,
            f"/books/{book_id}/accounts/{account_id}/register{query}",
        )

    def search(self, book_id: UUID, criteria: SearchCriteria) -> TransactionSearchOut:
        query = _query(
            {
                "q": (criteria.text or "").strip() or None,
                "amount": criteria.amount,
                "amount_min": criteria.amount_min,
                "amount_max": criteria.amount_max,
                "start": criteria.start,
                "end": criteria.end,
                "account_id": criteria.account_id,
                "include_children": criteria.include_children or None,
                "payee_id": criteria.payee_id,
                "limit": criteria.limit,
            }
        )
        return self._cached(
            book_id,
            f"search:{query}",
            TransactionSearchOut,
            f"/books/{book_id}/transactions/search{query}",
        )

    def transaction(self, book_id: UUID, transaction_id: UUID) -> TransactionOut:
        return self._cached(
            book_id,
            f"transaction:{transaction_id}",
            TransactionOut,
            f"/books/{book_id}/transactions/{transaction_id}",
        )

    def post_transaction(self, book_id: UUID, body: TransactionIn) -> TransactionOut:
        payload = self._call(
            "POST",
            f"/books/{book_id}/transactions",
            body.model_dump(mode="json"),
            book_id=book_id,
        )
        self._written(book_id)
        return TransactionOut.model_validate(payload)

    def update_transaction(
        self, book_id: UUID, transaction_id: UUID, body: TransactionUpdateIn
    ) -> TransactionOut:
        payload = self._call(
            "PATCH",
            f"/books/{book_id}/transactions/{transaction_id}",
            body.model_dump(mode="json", exclude_unset=True),
            book_id=book_id,
        )
        self._written(book_id)
        return TransactionOut.model_validate(payload)

    def delete_transaction(self, book_id: UUID, transaction_id: UUID) -> None:
        self._call(
            "DELETE", f"/books/{book_id}/transactions/{transaction_id}", book_id=book_id
        )
        self._written(book_id)

    def set_split_state(
        self,
        book_id: UUID,
        transaction_id: UUID,
        split_id: UUID,
        state: ReconcileStateName,
        on: date | None = None,
    ) -> TransactionOut:
        payload = self._call(
            "POST",
            f"/books/{book_id}/transactions/{transaction_id}/splits/{split_id}/reconcile",
            {"state": state.value, "on": None if on is None else on.isoformat()},
            book_id=book_id,
        )
        self._written(book_id)
        return TransactionOut.model_validate(payload)

    def reconcile_account(
        self,
        book_id: UUID,
        account_id: UUID,
        split_ids: list[UUID],
        state: ReconcileStateName,
        statement_date: date,
    ) -> None:
        self._call(
            "POST",
            f"/books/{book_id}/accounts/{account_id}/reconcile",
            {
                "split_ids": [str(s) for s in split_ids],
                "state": state.value,
                "statement_date": statement_date.isoformat(),
            },
            book_id=book_id,
        )
        self._written(book_id)

    def create_payee(self, book_id: UUID, body: PayeeIn) -> PayeeOut:
        payload = self._call(
            "POST",
            f"/books/{book_id}/payees",
            body.model_dump(mode="json"),
            book_id=book_id,
        )
        self._written(book_id)
        return PayeeOut.model_validate(payload)

    def create_account(self, book_id: UUID, body: AccountIn) -> AccountOut:
        payload = self._call(
            "POST",
            f"/books/{book_id}/accounts",
            body.model_dump(mode="json"),
            book_id=book_id,
        )
        self._written(book_id)
        return AccountOut.model_validate(payload)

    # -- connection --------------------------------------------------------------------
    def status(self) -> ConnectionStatus:
        return ConnectionStatus(
            online=self._online,
            mode="remote",
            last_sync=self._last_sync,
            last_error=self._last_error,
        )

    def sync(self) -> ConnectionStatus:
        with contextlib.suppress(OfflineError):
            self._call("GET", "/health")
        self._cache.clear()
        return self.status()

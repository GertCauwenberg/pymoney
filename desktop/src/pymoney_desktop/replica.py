"""Turning the server's sync batches back into rows of a local book.

The server's ``GET /changes`` hands out whole revision batches with the entities
already resolved (see :mod:`pymoney.core.sync`). A replica applies one batch as
one unit of work, table by table in dependency order -- commodities before the
accounts that embed them, accounts before the transactions that post to them --
and the deletions last. Applying a batch twice is the same as applying it once,
which is what lets a client that crashed mid-pull simply pull again.

The rows land with the *server's* audit stamps (``revision``, ``created_at``),
not with anything the local book would have assigned: the replica is a copy of
the server, and a local command's own revision counter is bookkeeping the
server never sees.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    BookOut,
    CommodityOut,
    PayeeOut,
    PriceOut,
    SyncBatchOut,
    TransactionOut,
)
from pymoney.core.account import Account, AccountType
from pymoney.core.amount import Amount
from pymoney.core.changelog import EntityKind
from pymoney.core.commodity import Commodity, CommodityKind
from pymoney.core.entity import EPOCH
from pymoney.core.payee import Payee
from pymoney.core.price import Price
from pymoney.core.rounding import RoundingMode
from pymoney.core.store import Store
from pymoney.core.transaction import ReconcileState, Split, Transaction

__all__ = ["apply_batch"]

_KINDS = {
    "COMMODITY": EntityKind.COMMODITY,
    "ACCOUNT": EntityKind.ACCOUNT,
    "PAYEE": EntityKind.PAYEE,
    "TRANSACTION": EntityKind.TRANSACTION,
    "PRICE": EntityKind.PRICE,
}


def _lookup(store: Store, commodity_id: UUID) -> Commodity:
    found = store.get(EntityKind.COMMODITY, commodity_id)
    if found is None:
        raise LookupError(
            f"batch refers to commodity {commodity_id}, which the replica does not have"
        )
    return found


def _commodity(out: CommodityOut, book_id: UUID) -> Commodity:
    return Commodity(
        id=out.id,
        book_id=book_id,
        code=out.code,
        kind=CommodityKind(out.kind),
        name=out.name,
        symbol=out.symbol,
        minor_unit_factor=out.minor_unit_factor,
        display_precision=out.display_precision,
        isin=out.isin,
        quote_source=out.quote_source,
        revision=out.revision,
    )


def _account(out: AccountOut, store: Store, book_id: UUID) -> Account:
    return Account(
        id=out.id,
        book_id=book_id,
        type=AccountType(out.type),
        name=out.name,
        commodity=_lookup(store, out.commodity_id),
        parent_id=out.parent_id,
        description=out.description,
        code=out.code,
        closed=out.closed,
        placeholder=out.placeholder,
        notes=out.notes,
        institution=out.institution,
        iban=out.iban,
        revision=out.revision,
    )


def _payee(out: PayeeOut, book_id: UUID) -> Payee:
    return Payee(
        id=out.id,
        book_id=book_id,
        name=out.name,
        notes=out.notes,
        address=out.address,
        match_keys=tuple(out.match_keys),
        default_account_id=out.default_account_id,
        revision=out.revision,
    )


def _price(out: PriceOut, store: Store, book_id: UUID) -> Price:
    return Price(
        id=out.id,
        book_id=book_id,
        base=_lookup(store, out.base_id),
        quote=_lookup(store, out.quote_id),
        num=out.rate.num,
        den=out.rate.den,
        effective_date=out.effective_date,
        as_of=out.as_of,
        source=out.source,
        revision=out.revision,
    )


def _transaction(out: TransactionOut, store: Store, book_id: UUID) -> Transaction:
    currency = _lookup(store, out.currency_id)
    splits = tuple(
        Split(
            id=s.id,
            account_id=s.account_id,
            quantity=Amount(_lookup(store, s.quantity.commodity_id), s.quantity.minor),
            value=Amount(currency, s.value.minor),
            memo=s.memo,
            payee_id=s.payee_id,
            reconcile_state=ReconcileState[s.reconcile_state.value],
            reconcile_date=s.reconcile_date,
            action=s.action,
            number=s.number,
            price=None if s.price is None else Fraction(s.price.num, s.price.den),
        )
        for s in out.splits
    )
    return Transaction(
        id=out.id,
        book_id=book_id,
        date=out.date,
        currency=currency,
        splits=splits,
        memo=out.memo,
        payee_id=out.payee_id,
        number=out.number,
        rounding=RoundingMode(out.rounding),
        entered_at=out.entered_at,
        imported_id=out.imported_id,
        revision=out.revision,
    )


def _put(store: Store, kind: EntityKind, entity: Any, at: Any) -> None:
    existing = store.get(kind, entity.id)
    stamped = replace(
        entity,
        created_at=existing.created_at
        if existing is not None and existing.created_at != EPOCH
        else at,
        updated_at=at,
    )
    store.put(kind, stamped, created=existing is None)


def apply_batch(store: Store, book_id: UUID, batch: SyncBatchOut) -> BookOut | None:
    """Apply one revision batch to ``store`` inside one unit of work.

    Returns the book settings if the batch changed them, so the caller can
    refresh whatever it keeps about the book.
    """
    store.begin()
    try:
        at = batch.at
        for c in batch.commodities:
            _put(store, EntityKind.COMMODITY, _commodity(c, book_id), at)
        for a in batch.accounts:
            _put(store, EntityKind.ACCOUNT, _account(a, store, book_id), at)
        for p in batch.payees:
            _put(store, EntityKind.PAYEE, _payee(p, book_id), at)
        for pr in batch.prices:
            _put(store, EntityKind.PRICE, _price(pr, store, book_id), at)
        for t in batch.transactions:
            _put(store, EntityKind.TRANSACTION, _transaction(t, store, book_id), at)
        # Deletions last: a transaction deleted in the same revision as the
        # account it posted to must go before the account can.
        for kind_name in ("TRANSACTION", "PRICE", "PAYEE", "ACCOUNT", "COMMODITY"):
            for d in batch.deletions:
                if d.kind == kind_name:
                    store.delete(_KINDS[kind_name], d.id)
        if batch.book is not None:
            row = store.load_book()
            store.save_book(
                replace(
                    row,
                    name=batch.book.name,
                    base_currency_id=batch.book.base_currency_id,
                    revision=max(row.revision, batch.revision),
                    settings_revision=batch.revision,
                )
            )
        store.commit()
    except Exception:
        store.rollback()
        raise
    return batch.book

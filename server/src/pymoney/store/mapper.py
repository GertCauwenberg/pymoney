"""Translation between domain objects and rows.

Kept in one file, and deliberately dumb. Every function here is either
``to_row`` or ``from_row``; none of them validates anything, because validation
belongs to the domain and a store that re-implemented it would eventually
disagree with it.

Loading needs a :class:`Registry` of commodities and accounts, since rows carry
ids where the domain carries objects. Those tables are small and already cached
by the store, so this costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any
from uuid import UUID

from ..core.account import Account, AccountType
from ..core.amount import Amount
from ..core.changelog import Change, ChangeOp, EntityKind
from ..core.commodity import Commodity, CommodityKind
from ..core.payee import Payee
from ..core.price import Price
from ..core.recurrence import Recurrence
from ..core.rounding import RoundingMode
from ..core.schedule import Schedule, ScheduleTemplate
from ..core.store import CommandRecord, StoredBook
from ..core.transaction import ReconcileState, Split, Transaction
from .types import fits_in_bigint

__all__ = ["Registry", "Rows"]


@dataclass(frozen=True, slots=True)
class Registry:
    """Lookups a row needs in order to become a domain object."""

    commodities: dict[UUID, Commodity]
    accounts: dict[UUID, Account]

    def commodity(self, commodity_id: UUID) -> Commodity:
        try:
            return self.commodities[commodity_id]
        except KeyError:  # pragma: no cover - a FK violation would precede this
            raise LookupError(
                f"row references unknown commodity {commodity_id}"
            ) from None


def _check_bigint(value: int, what: str) -> int:
    if not fits_in_bigint(value):
        raise ValueError(
            f"{what} does not fit in a 64-bit column: {value}. Prices are stored "
            f"as two BIGINTs; reduce the fraction or use a coarser rate."
        )
    return value


class Rows:
    """Namespace of row/object converters, one pair per table."""

    # -- books -------------------------------------------------------------
    @staticmethod
    def book_to_row(book: StoredBook, *, at: Any) -> dict[str, Any]:
        return {
            "id": book.id,
            "name": book.name,
            "base_currency_id": book.base_currency_id,
            "revision": book.revision,
            "settings_revision": book.settings_revision,
            "created_at": at,
            "updated_at": at,
        }

    @staticmethod
    def book_from_row(row: Any) -> StoredBook:
        return StoredBook(
            id=row.id,
            name=row.name,
            base_currency_id=row.base_currency_id,
            revision=row.revision,
            settings_revision=row.settings_revision,
        )

    # -- commodities -------------------------------------------------------
    @staticmethod
    def commodity_to_row(commodity: Commodity, book_id: UUID) -> dict[str, Any]:
        return {
            "id": commodity.id,
            "book_id": book_id,
            "kind": commodity.kind.value,
            "code": commodity.code,
            "code_key": commodity.key,
            "name": commodity.name,
            "symbol": commodity.symbol,
            "minor_unit_factor": _check_bigint(
                commodity.minor_unit_factor, "minor_unit_factor"
            ),
            "display_precision": commodity.display_precision,
            "isin": commodity.isin,
            "quote_source": commodity.quote_source,
            "created_at": commodity.created_at,
            "updated_at": commodity.updated_at,
            "revision": commodity.revision,
        }

    @staticmethod
    def commodity_from_row(row: Any) -> Commodity:
        return Commodity(
            id=row.id,
            book_id=row.book_id,
            kind=CommodityKind(row.kind),
            code=row.code,
            name=row.name,
            symbol=row.symbol,
            minor_unit_factor=row.minor_unit_factor,
            display_precision=row.display_precision,
            isin=row.isin,
            quote_source=row.quote_source,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- accounts ----------------------------------------------------------
    @staticmethod
    def account_to_row(account: Account, book_id: UUID) -> dict[str, Any]:
        return {
            "id": account.id,
            "book_id": book_id,
            "parent_id": account.parent_id,
            "type": account.type.value,
            "name": account.name,
            "name_key": account.key,
            "commodity_id": account.commodity.id,
            "description": account.description,
            "code": account.code,
            "closed": account.closed,
            "placeholder": account.placeholder,
            "notes": account.notes,
            "institution": account.institution,
            "iban": account.iban,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "revision": account.revision,
        }

    @staticmethod
    def account_from_row(row: Any, registry: Registry) -> Account:
        return Account(
            id=row.id,
            book_id=row.book_id,
            parent_id=row.parent_id,
            type=AccountType(row.type),
            name=row.name,
            commodity=registry.commodity(row.commodity_id),
            description=row.description,
            code=row.code,
            closed=row.closed,
            placeholder=row.placeholder,
            notes=row.notes,
            institution=row.institution,
            iban=row.iban,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- payees ------------------------------------------------------------
    @staticmethod
    def payee_to_row(payee: Payee, book_id: UUID) -> dict[str, Any]:
        return {
            "id": payee.id,
            "book_id": book_id,
            "name": payee.name,
            "name_key": payee.key,
            "notes": payee.notes,
            "address": payee.address,
            "match_keys": payee.match_keys,
            "default_account_id": payee.default_account_id,
            "created_at": payee.created_at,
            "updated_at": payee.updated_at,
            "revision": payee.revision,
        }

    @staticmethod
    def payee_from_row(row: Any) -> Payee:
        return Payee(
            id=row.id,
            book_id=row.book_id,
            name=row.name,
            notes=row.notes,
            address=row.address,
            match_keys=tuple(row.match_keys),
            default_account_id=row.default_account_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- schedules --------------------------------------------------------
    @staticmethod
    def schedule_to_row(schedule: Schedule, book_id: UUID) -> dict[str, Any]:
        assert schedule.template is not None and schedule.recurrence is not None
        return {
            "id": schedule.id,
            "book_id": book_id,
            "name": schedule.name,
            "recurrence": schedule.recurrence.to_dict(),
            "template": schedule.template.to_dict(),
            "occurrences_done": schedule.occurrences_done,
            "next_due": schedule.next_due,
            "last_occurrence": schedule.last_occurrence,
            "auto_post": schedule.auto_post,
            "paused": schedule.paused,
            "created_at": schedule.created_at,
            "updated_at": schedule.updated_at,
            "revision": schedule.revision,
        }

    @staticmethod
    def schedule_from_row(row: Any) -> Schedule:
        return Schedule(
            id=row.id,
            book_id=row.book_id,
            name=row.name,
            recurrence=Recurrence.from_dict(dict(row.recurrence)),
            template=ScheduleTemplate.from_dict(dict(row.template)),
            occurrences_done=row.occurrences_done,
            last_occurrence=row.last_occurrence,
            auto_post=row.auto_post,
            paused=row.paused,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- transactions and splits -------------------------------------------
    @staticmethod
    def transaction_to_row(transaction: Transaction, book_id: UUID) -> dict[str, Any]:
        return {
            "id": transaction.id,
            "book_id": book_id,
            "post_date": transaction.date,
            "currency_id": transaction.currency.id,
            "memo": transaction.memo,
            "payee_id": transaction.payee_id,
            "number": transaction.number,
            "rounding": transaction.rounding.value,
            "entered_at": transaction.entered_at,
            "imported_id": transaction.imported_id,
            "created_at": transaction.created_at,
            "updated_at": transaction.updated_at,
            "revision": transaction.revision,
        }

    @staticmethod
    def split_to_row(
        split: Split, transaction: Transaction, book_id: UUID, position: int
    ) -> dict[str, Any]:
        price = split.price
        return {
            "id": split.id,
            "book_id": book_id,
            "transaction_id": transaction.id,
            "position": position,
            "account_id": split.account_id,
            # Denormalised so the hot index covers account + date.
            "post_date": transaction.date,
            "quantity_commodity_id": split.quantity.commodity.id,
            "quantity_minor": _check_bigint(split.quantity.minor, "quantity"),
            "value_minor": _check_bigint(split.value.minor, "value"),
            "memo": split.memo,
            "payee_id": split.payee_id,
            "reconcile_state": int(split.reconcile_state),
            "reconcile_date": split.reconcile_date,
            "action": split.action,
            "number": split.number,
            "price_num": None
            if price is None
            else _check_bigint(price.numerator, "price numerator"),
            "price_den": None
            if price is None
            else _check_bigint(price.denominator, "price denominator"),
        }

    @staticmethod
    def transaction_from_rows(
        row: Any, split_rows: list[Any], registry: Registry
    ) -> Transaction:
        currency = registry.commodity(row.currency_id)
        splits = tuple(
            Split(
                id=s.id,
                account_id=s.account_id,
                quantity=Amount(
                    registry.commodity(s.quantity_commodity_id), s.quantity_minor
                ),
                value=Amount(currency, s.value_minor),
                memo=s.memo,
                payee_id=s.payee_id,
                reconcile_state=ReconcileState(s.reconcile_state),
                reconcile_date=s.reconcile_date,
                action=s.action,
                number=s.number,
                price=None if s.price_num is None else Fraction(s.price_num, s.price_den),
            )
            for s in sorted(split_rows, key=lambda s: s.position)
        )
        return Transaction(
            id=row.id,
            book_id=row.book_id,
            date=row.post_date,
            currency=currency,
            splits=splits,
            memo=row.memo,
            payee_id=row.payee_id,
            number=row.number,
            rounding=RoundingMode(row.rounding),
            entered_at=row.entered_at,
            imported_id=row.imported_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- prices ------------------------------------------------------------
    @staticmethod
    def price_to_row(price: Price, book_id: UUID) -> dict[str, Any]:
        return {
            "id": price.id,
            "book_id": book_id,
            "base_id": price.base.id,
            "quote_id": price.quote.id,
            "num": _check_bigint(price.num, "price numerator"),
            "den": _check_bigint(price.den, "price denominator"),
            "effective_date": price.effective_date,
            "as_of": price.as_of,
            "source": price.source,
            "created_at": price.created_at,
            "updated_at": price.updated_at,
            "revision": price.revision,
        }

    @staticmethod
    def price_from_row(row: Any, registry: Registry) -> Price:
        return Price(
            id=row.id,
            book_id=row.book_id,
            base=registry.commodity(row.base_id),
            quote=registry.commodity(row.quote_id),
            num=row.num,
            den=row.den,
            effective_date=row.effective_date,
            as_of=row.as_of,
            source=row.source,
            created_at=row.created_at,
            updated_at=row.updated_at,
            revision=row.revision,
        )

    # -- applied commands --------------------------------------------------
    @staticmethod
    def command_to_row(
        record: CommandRecord, book_id: UUID, *, at: Any
    ) -> dict[str, Any]:
        return {
            "book_id": book_id,
            "command_id": record.command_id,
            "fingerprint": record.fingerprint,
            "revision": record.revision,
            "command": record.command,
            "entity_kind": None
            if record.entity_kind is None
            else record.entity_kind.value,
            "entity_id": record.entity_id,
            "at": at,
        }

    @staticmethod
    def command_from_row(row: Any) -> CommandRecord:
        return CommandRecord(
            command_id=row.command_id,
            fingerprint=row.fingerprint,
            revision=row.revision,
            command=row.command,
            entity_kind=None if row.entity_kind is None else EntityKind(row.entity_kind),
            entity_id=row.entity_id,
        )

    # -- change log --------------------------------------------------------
    @staticmethod
    def change_to_row(change: Change, book_id: UUID, seq: int) -> dict[str, Any]:
        return {
            "book_id": book_id,
            "revision": change.revision,
            "seq": seq,
            "at": change.at,
            "kind": change.kind.value,
            "entity_id": change.entity_id,
            "op": change.op.value,
            "command": change.command,
            "command_id": change.command_id,
            "actor": change.actor,
            "note": change.note,
        }

    @staticmethod
    def change_from_row(row: Any) -> Change:
        return Change(
            revision=row.revision,
            at=row.at,
            kind=EntityKind(row.kind),
            entity_id=row.entity_id,
            op=ChangeOp(row.op),
            command=row.command,
            command_id=row.command_id,
            actor=row.actor,
            note=row.note,
        )

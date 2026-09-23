"""Wire schemas. Deliberately not the domain objects.

Serialisation has different concerns from a ledger, and three of them decide the
shape of everything here.

**A JSON number is a float.** ``0.1`` in a JSON body is not one tenth, and
``9007199254740993`` does not survive a round trip through a browser. So every
amount and rate crosses the wire as a **string or an integer, never a JSON
float**, and :data:`Exact` rejects floats outright rather than quietly rounding.
That is the same rule the domain applies to ``to_fraction``, carried to the edge.

**An amount is not a number.** It is a quantity of a commodity, and a client that
renders it needs the commodity and its scale too. :class:`AmountOut` therefore
carries the exact minor units (for arithmetic), the commodity, and a formatted
string (for display) -- so a client never has to guess a scale or re-implement
rounding.

**A rate is a ratio.** :class:`RateOut` sends ``num``/``den`` as integers, which
is lossless, plus a decimal approximation marked as such. A client that only
displays prices uses the approximation; one that computes with them uses the
pair.

Input models are separate from output models throughout. They diverge -- input has
no ids or audit stamps, output has no write-only fields -- and a single model
pretending otherwise is how optional fields multiply.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..core.account import Account, AccountClass, AccountType
from ..core.amount import Amount
from ..core.commodity import Commodity, CommodityKind
from ..core.payee import Payee
from ..core.price import Price
from ..core.recurrence import Frequency as _Frequency
from ..core.recurrence import Recurrence as _Recurrence
from ..core.rounding import RoundingMode
from ..core.schedule import Schedule as _Schedule
from ..core.schedule import ScheduleTemplate as _ScheduleTemplate
from ..core.schedule import TemplateLeg as _TemplateLeg
from ..core.settings import BookSettings
from ..core.tag import Tag
from ..core.transaction import ReconcileState, Split, Transaction
from ..importers.statement import AmountKind as _AmountKind
from ..importers.statement import ColumnMapping as _ColumnMapping
from ..importers.statement import RowAction as _RowAction
from ..importers.statement import RowDecision as _RowDecision

__all__ = [
    "AccountIn",
    "AccountOut",
    "ActivateIn",
    "AmountOut",
    "BookIn",
    "BookOut",
    "ChangeOut",
    "ColumnMappingModel",
    "CommodityIn",
    "CommodityOut",
    "CommodityPatch",
    "CsvInspectOut",
    "CsvPreviewOut",
    "DeleteBookIn",
    "DeleteMeIn",
    "DuePanelOut",
    "DueScheduleOut",
    "EmailIn",
    "Exact",
    "ImportDecisionIn",
    "ImportOut",
    "ImportProblemOut",
    "ImportProposalOut",
    "LegIn",
    "LoginIn",
    "MergeAccountsIn",
    "MergePayeesIn",
    "MoveAccountIn",
    "Page",
    "PayeeIn",
    "PayeeOut",
    "PendingOut",
    "PostOccurrenceIn",
    "PriceIn",
    "PriceOut",
    "PricePage",
    "QuoteFetchIn",
    "QuoteProviderOut",
    "QuoteRunOut",
    "RateOut",
    "ReconcileIn",
    "ReconcileStateName",
    "RecurrenceModel",
    "RegisterRowOut",
    "RegisterTotals",
    "RenameIn",
    "ResetPasswordIn",
    "ScheduleIn",
    "ScheduleOut",
    "SchedulePatch",
    "ScheduleTemplateIn",
    "ScheduleTemplateOut",
    "SessionOut",
    "SkipOccurrenceIn",
    "SplitOut",
    "StatementImportOut",
    "SyncBatchOut",
    "SyncPageOut",
    "TemplateLegIn",
    "TemplateLegOut",
    "TransactionIn",
    "TransactionOut",
    "TransactionPage",
    "TransactionUpdateIn",
    "UserOut",
]


# ==========================================================================
# exact numbers
# ==========================================================================
# Aliases for the models that have a field *called* `date`. With postponed
# annotation evaluation, `date: date` resolves the annotation inside the class
# namespace, where `date` is now the field -- so the alias is not cosmetic.
PostDate = date
Instant = datetime


class ReconcileStateName(StrEnum):
    """Reconciliation state, by name.

    The domain's ``ReconcileState`` is an ``IntEnum`` -- deliberately ordered, so
    "at least cleared" is a comparison. That is right in the ledger and wrong on
    the wire: a client reading ``2`` learns nothing, and a future state inserted
    in the middle would silently change the meaning of stored integers. So the
    API speaks names and converts at the boundary. This is the clearest small
    example of why the schemas are not the domain objects.
    """

    NOT_RECONCILED = "NOT_RECONCILED"
    CLEARED = "CLEARED"
    RECONCILED = "RECONCILED"
    FROZEN = "FROZEN"

    def to_domain(self) -> ReconcileState:
        return ReconcileState[self.value]

    @classmethod
    def of(cls, state: ReconcileState) -> ReconcileStateName:
        return cls[state.name]


def _reject_float(value: Any) -> Any:
    """The single most important validator in this module.

    A float has already lost the information by the time we see it, so there is
    nothing to do but refuse and say why.
    """
    if isinstance(value, float):
        raise ValueError(
            'send an exact value as a string ("12.34") or an integer, not a '
            "JSON number: binary floats cannot represent most decimal amounts"
        )
    return value


def _exact(value: Any) -> Any:
    """Reject a float, and any string that is not an exact, finite number.

    Without this the parse happened later, in :func:`to_fraction` inside a
    route handler, where ``"1/0"`` raised :class:`ZeroDivisionError` (a sibling
    of :class:`ValueError`, so *not* caught by the 422 mapping) and surfaced as
    a 500. Validating here turns every malformed amount into an ordinary 422
    with the offending field named.
    """
    value = _reject_float(value)
    if isinstance(value, bool):
        raise ValueError("send an exact value as a string or an integer, not a boolean")
    if isinstance(value, str):
        try:
            Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(
                f"{value!r} is not an exact number: give a decimal string "
                '("12.34"), an integer, or a ratio ("10/3")'
            ) from exc
    elif isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("an exact value must be finite, not NaN or infinity")
    return value


Exact = Annotated[
    Decimal | int | str,
    BeforeValidator(_exact),
    Field(
        examples=["12.34", "-1000", "1/3"],
        description=(
            "An exact quantity in major units. A string, or an integer. "
            "Never a JSON float."
        ),
    ),
]

RateLike = Annotated[
    Decimal | int | str,
    BeforeValidator(_exact),
    Field(
        examples=["0.9174", "10/9"],
        description="An exact positive rate. A decimal string or a 'num/den' ratio.",
    ),
]


def to_fraction(value: Decimal | int | str) -> Fraction:
    """Wire value to exact rational. ``"10/3"`` and ``"3.33"`` both work.

    Inputs are validated by :func:`_exact` before they reach here, but the
    conversion is defended anyway: a stray ``ZeroDivisionError`` or a non-finite
    ``Decimal`` must come out as a 422, never a 500.
    """
    try:
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"{value} is not a finite number")
            return Fraction(value)
        return Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"{value!r} is not an exact number") from exc


# ==========================================================================
# output building blocks
# ==========================================================================
class Out(BaseModel):
    """Base for response models."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


class In(BaseModel):
    """Base for request models. Unknown fields are an error, not a shrug.

    A typo in a field name would otherwise be silently ignored and the user
    would wonder why their memo did not save.
    """

    model_config = ConfigDict(extra="forbid")


class AmountOut(Out):
    """A booked amount: exact minor units, plus what to show a human."""

    commodity_id: UUID
    commodity: str = Field(description="The commodity's code, e.g. 'EUR'.")
    minor: int = Field(
        description="Exact count of the commodity's smallest unit. Authoritative."
    )
    scale: int = Field(description="Minor units per major unit, e.g. 100 for EUR.")
    text: str = Field(description="Formatted for display. Derived, not authoritative.")

    @classmethod
    def of(cls, amount: Amount) -> AmountOut:
        return cls(
            commodity_id=amount.commodity.id,
            commodity=amount.commodity.code,
            minor=amount.minor,
            scale=amount.commodity.minor_unit_factor,
            text=amount.format(symbol=False),
        )


class RateOut(Out):
    """An exact rate, as a ratio plus a lossy convenience decimal."""

    num: int
    den: int
    approx: str = Field(
        description="Decimal approximation to 10 places. For display only."
    )

    @classmethod
    def of(cls, rate: Fraction) -> RateOut:
        return cls(
            num=rate.numerator,
            den=rate.denominator,
            approx=f"{Decimal(rate.numerator) / Decimal(rate.denominator):.10f}",
        )


class Page(Out):
    """One page of a list. Keyset cursors, so inserts cannot shift a page.

    Subclassed per item type, so the OpenAPI document says what is in a page
    and a generated client does not get ``unknown[]``.
    """

    items: list[Any]
    next_cursor: str | None = Field(
        default=None,
        description="Pass as `cursor` for the next page. Null when exhausted.",
    )
    has_more: bool = False


# ==========================================================================
# books
# ==========================================================================
class BookIn(In):
    name: str = Field(min_length=1, max_length=255)
    base_currency_code: str = Field(min_length=1, max_length=32, examples=["EUR"])
    base_currency_name: str = ""
    base_currency_scale: int = Field(
        default=2,
        ge=0,
        le=12,
        description="Decimal places. 2 for EUR, 0 for JPY, 3 for KWD.",
    )
    base_currency_symbol: str | None = None


class ImportProblemOut(Out):
    level: str
    category: str
    message: str
    source_id: str


class ImportOut(Out):
    """A book made from an uploaded file, and what the importer had to say."""

    book: BookOut
    ok: bool = Field(description="No errors. Warnings and notes may still be present.")
    imported: dict[str, int] = Field(description="How many of each thing was created.")
    skipped: dict[str, int] = Field(
        description="What the file had that PyMoney has no home for yet."
    )
    problems: list[ImportProblemOut]
    adjustments: list[str] = Field(
        description="Rounding corrections posted, one line each."
    )
    summary: str


class DeleteBookIn(In):
    name: str = Field(
        description="The book's exact name, repeated, to confirm an irreversible deletion."
    )


class BookOut(Out):
    id: UUID
    name: str
    base_currency_id: UUID | None
    revision: int = Field(
        description="The book's current revision: a client's sync watermark."
    )
    role: str | None = Field(default=None, description="The caller's role on this book.")

    @classmethod
    def of(
        cls, settings: BookSettings, revision: int, role: str | None = None
    ) -> BookOut:
        return cls(
            id=settings.id,
            name=settings.name,
            base_currency_id=settings.base_currency_id,
            revision=revision,
            role=role,
        )


# ==========================================================================
# commodities
# ==========================================================================
class CommodityIn(In):
    code: str = Field(min_length=1, max_length=32)
    name: str = ""
    kind: CommodityKind = CommodityKind.CURRENCY
    scale: int | None = Field(
        default=None,
        ge=0,
        le=12,
        description="Decimal places; sets minor_unit_factor to 10**scale.",
    )
    minor_unit_factor: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Smallest units per major unit, for commodities that are not a power "
            "of ten (5 for the Mauritanian ouguiya). Overrides `scale`."
        ),
    )
    display_precision: int | None = Field(default=None, ge=0, le=12)
    symbol: str | None = None
    isin: str | None = None
    quote_source: str | None = None
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id (UUIDv7 recommended). An offline client applies the "
            "command locally first and replays it later; with the id fixed, the "
            "server's copy and the local one are the same row. 409 if taken."
        ),
    )

    def factor(self) -> int:
        if self.minor_unit_factor is not None:
            return self.minor_unit_factor
        return 10 ** (self.scale if self.scale is not None else 2)


class CommodityPatch(In):
    """The display fields of a commodity.

    Not ``minor_unit_factor``: every booked amount is a count of minor units,
    so changing the factor would reinterpret all of them. Create a new commodity
    instead.
    """

    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = None
    symbol: str | None = None
    display_precision: int | None = Field(default=None, ge=0, le=12)
    isin: str | None = None
    quote_source: str | None = None


class CommodityOut(Out):
    id: UUID
    code: str
    name: str
    kind: CommodityKind
    symbol: str | None
    minor_unit_factor: int
    display_precision: int
    isin: str | None
    quote_source: str | None
    revision: int

    @classmethod
    def of(cls, commodity: Commodity) -> CommodityOut:
        return cls(
            id=commodity.id,
            code=commodity.code,
            name=commodity.name,
            kind=commodity.kind,
            symbol=commodity.symbol,
            minor_unit_factor=commodity.minor_unit_factor,
            display_precision=commodity.precision,
            isin=commodity.isin,
            quote_source=commodity.quote_source,
            revision=commodity.revision,
        )


# ==========================================================================
# accounts
# ==========================================================================
class AccountIn(In):
    name: str = Field(min_length=1, max_length=255)
    type: AccountType
    parent_id: UUID | None = None
    commodity_id: UUID | None = Field(
        default=None, description="Defaults to the book's base currency."
    )
    description: str = ""
    code: str = ""
    placeholder: bool = False
    notes: str = ""
    institution: str = ""
    iban: str = ""
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id (UUIDv7 recommended). An offline client applies the "
            "command locally first and replays it later; with the id fixed, the "
            "server's copy and the local one are the same row. 409 if taken."
        ),
    )


class AccountPatch(In):
    """Only the fields a plain update may touch.

    Renames, moves, closures, commodity changes and merges are separate
    endpoints, because each has its own rules and its own failure modes.
    """

    description: str | None = None
    code: str | None = None
    notes: str | None = None
    institution: str | None = None
    iban: str | None = None
    placeholder: bool | None = None


class AccountOut(Out):
    id: UUID
    name: str
    path: str = Field(description="Colon-joined full name. Derived from the tree.")
    type: AccountType
    account_class: AccountClass
    parent_id: UUID | None
    commodity_id: UUID
    commodity: str
    description: str
    code: str
    closed: bool
    placeholder: bool
    notes: str
    institution: str
    iban: str
    revision: int

    @classmethod
    def of(cls, account: Account, path: str) -> AccountOut:
        return cls(
            id=account.id,
            name=account.name,
            path=path,
            type=account.type,
            account_class=account.account_class,
            parent_id=account.parent_id,
            commodity_id=account.commodity.id,
            commodity=account.commodity.code,
            description=account.description,
            code=account.code,
            closed=account.closed,
            placeholder=account.placeholder,
            notes=account.notes,
            institution=account.institution,
            iban=account.iban,
            revision=account.revision,
        )


class AccountBalanceOut(Out):
    account_id: UUID
    path: str
    balance: AmountOut
    subtree: list[AmountOut] = Field(
        description="The account plus its descendants, one entry per commodity."
    )


class RenameIn(In):
    name: str = Field(min_length=1, max_length=255)


class MoveAccountIn(In):
    parent_id: UUID | None = Field(
        description="New parent, or null to move to the top level."
    )


class SetClosedIn(In):
    closed: bool = True


class SetCommodityIn(In):
    commodity_id: UUID


class MergeAccountsIn(In):
    source_id: UUID = Field(description="Emptied and removed.")


# ==========================================================================
# payees
# ==========================================================================
class PayeeIn(In):
    name: str = Field(min_length=1, max_length=255)
    notes: str = ""
    address: str = ""
    match_keys: list[str] = Field(default_factory=list)
    default_account_id: UUID | None = None
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id (UUIDv7 recommended). An offline client applies the "
            "command locally first and replays it later; with the id fixed, the "
            "server's copy and the local one are the same row. 409 if taken."
        ),
    )


class PayeePatch(In):
    name: str | None = None
    notes: str | None = None
    address: str | None = None
    match_keys: list[str] | None = None
    default_account_id: UUID | None = None


class PayeeOut(Out):
    id: UUID
    name: str
    notes: str
    address: str
    match_keys: list[str]
    default_account_id: UUID | None
    revision: int

    @classmethod
    def of(cls, payee: Payee) -> PayeeOut:
        return cls(
            id=payee.id,
            name=payee.name,
            notes=payee.notes,
            address=payee.address,
            match_keys=list(payee.match_keys),
            default_account_id=payee.default_account_id,
            revision=payee.revision,
        )


class MergePayeesIn(In):
    source_ids: list[UUID] = Field(min_length=1)
    keep_match_keys: bool = True


# ==========================================================================
# tags
# ==========================================================================
_COLOUR = Field(
    default=None,
    max_length=16,
    description="An optional CSS colour, e.g. #ff8800.",
)


class TagIn(In):
    name: str = Field(min_length=1, max_length=255)
    notes: str = ""
    colour: str | None = _COLOUR
    closed: bool = False
    id: UUID | None = Field(
        default=None,
        description="Client-chosen id, for the offline client to fix on replay.",
    )


class TagPatch(In):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = None
    colour: str | None = None
    closed: bool | None = None


class TagOut(Out):
    id: UUID
    name: str
    notes: str
    colour: str | None
    closed: bool
    revision: int

    @classmethod
    def of(cls, tag: Tag) -> TagOut:
        return cls(
            id=tag.id,
            name=tag.name,
            notes=tag.notes,
            colour=tag.colour,
            closed=tag.closed,
            revision=tag.revision,
        )


# ==========================================================================
# transactions
# ==========================================================================
class LegIn(In):
    """One leg, as the client means it, before any rounding.

    Supply any two of ``value``, ``quantity`` and ``price``; the third is
    derived. Exactly one leg may leave all three unset, making it the residual
    leg that absorbs whatever balances the entry.
    """

    account_id: UUID
    value: Exact | None = Field(
        default=None, description="Worth in the transaction currency."
    )
    quantity: Exact | None = Field(
        default=None, description="Movement in the account's own commodity."
    )
    price: RateLike | None = Field(
        default=None, description="Value per unit of quantity."
    )
    memo: str = ""
    payee_id: UUID | None = None
    action: str = ""
    number: str = ""
    reconcile_state: ReconcileStateName = ReconcileStateName.NOT_RECONCILED
    reconcile_date: date | None = None
    tag_ids: list[UUID] = Field(
        default_factory=list,
        description="Tags to put on this leg; duplicates are collapsed.",
    )


class TransactionIn(In):
    date: PostDate = Field(description="Post date. A plain date, no timezone.")
    legs: list[LegIn] = Field(min_length=1)
    currency_id: UUID | None = Field(
        default=None, description="Defaults to the book's base currency."
    )
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode = RoundingMode.HALF_EVEN
    imbalance_account_id: UUID | None = Field(
        default=None,
        description=(
            "If set, a leg is appended to absorb any residual instead of the "
            "request being rejected as unbalanced."
        ),
    )
    imported_id: str | None = Field(
        default=None, description="External reference, unique per book."
    )
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id (UUIDv7 recommended). An offline client applies the "
            "command locally first and replays it later; with the id fixed, the "
            "server's copy and the local one are the same row. 409 if taken."
        ),
    )


class TransactionUpdateIn(In):
    """Splits are replaced wholesale, never patched.

    A partial split edit can leave a transaction unbalanced, and an unbalanced
    transaction must not exist even for an instant.
    """

    date: PostDate | None = None
    legs: list[LegIn] | None = None
    memo: str | None = None
    payee_id: UUID | None = None
    number: str | None = None
    currency_id: UUID | None = None
    rounding: RoundingMode | None = None
    imbalance_account_id: UUID | None = None


class SplitOut(Out):
    id: UUID
    account_id: UUID
    quantity: AmountOut
    value: AmountOut
    memo: str
    payee_id: UUID | None
    reconcile_state: ReconcileStateName
    reconcile_date: date | None
    action: str
    number: str
    price: RateOut | None = Field(
        default=None, description="The rate the client stated, if any."
    )
    implied_price: RateOut | None = Field(
        default=None,
        description="value / quantity, from the booked figures. Null if quantity is 0.",
    )
    tag_ids: list[UUID] = Field(
        default_factory=list, description="Tags on this leg, in the user's order."
    )

    @classmethod
    def of(cls, split: Split) -> SplitOut:
        implied = split.implied_price
        return cls(
            id=split.id,
            account_id=split.account_id,
            quantity=AmountOut.of(split.quantity),
            value=AmountOut.of(split.value),
            memo=split.memo,
            payee_id=split.payee_id,
            reconcile_state=ReconcileStateName.of(split.reconcile_state),
            reconcile_date=split.reconcile_date,
            action=split.action,
            number=split.number,
            price=None if split.price is None else RateOut.of(split.price),
            implied_price=None if implied is None else RateOut.of(implied),
            tag_ids=list(split.tag_ids),
        )


class AttachmentOut(Out):
    """A receipt/invoice/scan pinned to a transaction. The bytes are fetched
    separately from ``GET /books/{id}/attachments/{attachment_id}``."""

    id: UUID
    transaction_id: UUID
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    uploaded_by: str
    uploaded_at: datetime

    @classmethod
    def of(cls, row: Any) -> AttachmentOut:
        return cls(
            id=row.id,
            transaction_id=row.transaction_id,
            filename=row.filename,
            content_type=row.content_type,
            byte_size=row.byte_size,
            sha256=row.sha256,
            uploaded_by=row.uploaded_by,
            uploaded_at=row.uploaded_at,
        )


class TransactionOut(Out):
    id: UUID
    date: PostDate
    currency_id: UUID
    currency: str
    memo: str
    payee_id: UUID | None
    number: str
    rounding: RoundingMode
    splits: list[SplitOut]
    entered_at: datetime | None
    imported_id: str | None
    reconcile_state: ReconcileStateName = Field(
        description="The strongest state among the splits. Locks edits at RECONCILED."
    )
    locked: bool
    is_stock_split: bool = Field(
        description=(
            "A single-leg transaction: a change in quantity with no value. "
            "Recorded and deleted, not edited."
        )
    )
    revision: int

    @classmethod
    def of(cls, transaction: Transaction) -> TransactionOut:
        return cls(
            id=transaction.id,
            date=transaction.date,
            currency_id=transaction.currency.id,
            currency=transaction.currency.code,
            memo=transaction.memo,
            payee_id=transaction.payee_id,
            number=transaction.number,
            rounding=transaction.rounding,
            splits=[SplitOut.of(s) for s in transaction.splits],
            entered_at=transaction.entered_at,
            imported_id=transaction.imported_id,
            reconcile_state=ReconcileStateName.of(transaction.lock_state),
            locked=transaction.is_locked,
            is_stock_split=transaction.is_stock_split,
            revision=transaction.revision,
        )


class TransactionSearchOut(Out):
    """What a search found, newest first."""

    items: list[TransactionOut]
    has_more: bool = Field(
        description="True when the limit cut the list short; narrow the search."
    )
    limit: int


class TransactionPage(Page):
    items: list[TransactionOut]


class ReconcileIn(In):
    split_ids: list[UUID] = Field(default_factory=list)
    state: ReconcileStateName = ReconcileStateName.RECONCILED
    statement_date: date | None = None


class SetSplitStateIn(In):
    state: ReconcileStateName
    on: date | None = None


class StockSplitIn(In):
    """A share split on an account's holding.

    ``ratio`` is new-for-old: ``"2"`` doubles the shares (two-for-one),
    ``"1/3"`` is a one-for-three reverse split. It posts a single-leg
    transaction with the change in quantity and no value, and -- where one is
    on file -- a split-adjusted price.
    """

    date: PostDate
    ratio: RateLike
    memo: str = ""
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id for the posted transaction, so an offline "
            "client and the server converge on one row. 409 if taken."
        ),
    )


# ==========================================================================
# register
# ==========================================================================
class ConversionOut(Out):
    base: str
    quote: str
    rate: RateOut
    hops: int
    path: list[str] = Field(description="The commodities chained through.")
    sources: list[str]


class RegisterRowOut(Out):
    transaction_id: UUID
    split_id: UUID
    date: PostDate
    memo: str = Field(description="The split's memo, falling back to the transaction's.")
    payee_id: UUID | None
    account_id: UUID
    quantity: AmountOut
    value: AmountOut
    reconcile_state: ReconcileStateName
    number: str
    running_balance: AmountOut | None = Field(
        default=None,
        description=(
            "Balance after this row, within the requested window only. Null when "
            "the rows span more than one commodity."
        ),
    )


class RegisterTotals(Out):
    opening: AmountOut | None = Field(
        default=None, description="Balance before the window."
    )
    closing: AmountOut | None = Field(
        default=None, description="Balance at the end of the window."
    )
    translated: bool = Field(
        default=False,
        description=(
            "True when sub-accounts in other commodities were translated into the "
            "account's commodity, at one rate: the window's end date (or today). "
            "The running balance and both totals are then valuations, not sums."
        ),
    )
    conversions: list[ConversionOut] = Field(
        default_factory=list, description="The rates used for that translation."
    )
    unpriced: list[str] = Field(
        default_factory=list,
        description=(
            "Commodities of sub-accounts that could not be translated. Their rows "
            "show no running balance, and the totals are null."
        ),
    )


class RegisterPage(Out):
    items: list[RegisterRowOut]
    next_cursor: str | None = None
    has_more: bool = False
    totals: RegisterTotals


# ==========================================================================
# prices
# ==========================================================================
class PriceIn(In):
    base_id: UUID
    quote_id: UUID
    rate: RateLike
    effective_date: date = Field(description="The date the rate applies to.")
    as_of: datetime | None = Field(
        default=None,
        description=(
            "When the rate became known. Defaults to now. A correction is a new "
            "row with a later as_of, never an edit."
        ),
    )
    source: str = "user"
    id: UUID | None = Field(
        default=None,
        description=(
            "Client-chosen id (UUIDv7 recommended). An offline client applies the "
            "command locally first and replays it later; with the id fixed, the "
            "server's copy and the local one are the same row. 409 if taken."
        ),
    )


class PriceOut(Out):
    id: UUID
    base_id: UUID
    base: str
    quote_id: UUID
    quote: str
    rate: RateOut
    effective_date: date
    as_of: datetime
    source: str
    revision: int

    @classmethod
    def of(cls, price: Price) -> PriceOut:
        return cls(
            id=price.id,
            base_id=price.base.id,
            base=price.base.name,
            quote_id=price.quote.id,
            quote=price.quote.name,
            rate=RateOut.of(price.rate),
            effective_date=price.effective_date,
            as_of=price.as_of,
            source=price.source,
            revision=price.revision,
        )


class PricePage(Page):
    items: list[PriceOut]


# ==========================================================================
# reports
# ==========================================================================
class ReportRowOut(Out):
    account_id: UUID
    path: str
    depth: int
    value: AmountOut = Field(description="Subtree total, signed as stored.")
    display_value: AmountOut = Field(
        description="Signed to read naturally: debt and income come out positive."
    )
    own_value: AmountOut


class BalanceSheetOut(Out):
    as_of: date
    target: str
    assets: AmountOut
    liabilities: AmountOut = Field(description="Signed as stored, so normally negative.")
    liabilities_owed: AmountOut
    equity: AmountOut
    net_worth: AmountOut
    rows: list[ReportRowOut]
    conversions: list[ConversionOut] = Field(
        description="Every rate used, so a total can be traced to its sources."
    )
    unpriced: list[str] = Field(
        description="Commodities that could not be valued (lenient mode only)."
    )


class IncomeStatementOut(Out):
    start: date
    end: date
    target: str
    income: AmountOut
    income_earned: AmountOut
    expenses: AmountOut
    net: AmountOut
    savings_rate: RateOut | None
    rows: list[ReportRowOut]
    conversions: list[ConversionOut]
    unpriced: list[str]


class HoldingOut(Out):
    account_id: UUID
    path: str
    commodity: str
    quantity: AmountOut
    cost: AmountOut
    market_value: AmountOut
    unrealised_gain: AmountOut | None = Field(
        default=None,
        description="Null unless cost and market value are in the same commodity.",
    )
    unit_price: RateOut | None


class HoldingsOut(Out):
    as_of: date
    target: str
    positions: list[HoldingOut]
    total_value: AmountOut
    total_cost: AmountOut


ReportInterval = Literal["month", "quarter", "year"]


class PeriodOut(Out):
    start: date
    end: date
    label: str = Field(description="`2026-03`, `2026-Q1` or `2026`.")


class NetWorthPointOut(Out):
    as_of: date
    label: str
    assets: AmountOut
    liabilities: AmountOut = Field(description="Signed as stored, so normally negative.")
    net_worth: AmountOut


class NetWorthSeriesOut(Out):
    """Assets, liabilities and net worth at the end of every period: the line chart."""

    start: date
    end: date
    interval: ReportInterval
    target: str
    points: list[NetWorthPointOut]
    conversions: list[ConversionOut] = Field(
        description="Every distinct rate used at any point of the series."
    )
    unpriced: list[str]


class PeriodRowOut(Out):
    account_id: UUID
    path: str
    depth: int
    cells: list[AmountOut] = Field(
        description="Subtree movement per column, signed as stored."
    )
    display_cells: list[AmountOut] = Field(
        description="The same, signed to read naturally (income positive)."
    )
    total: AmountOut
    display_total: AmountOut


class PeriodTableOut(Out):
    """Income and expense accounts down, periods across: the spreadsheet view."""

    start: date
    end: date
    interval: ReportInterval
    target: str
    columns: list[PeriodOut]
    rows: list[PeriodRowOut]
    income: list[AmountOut] = Field(
        description="Per column, signed as stored; the last entry is the total."
    )
    income_earned: list[AmountOut] = Field(description="Income as positive figures.")
    expenses: list[AmountOut]
    net: list[AmountOut] = Field(description="Surplus per column, positive when saving.")
    conversions: list[ConversionOut]
    unpriced: list[str]


# ==========================================================================
# sync
# ==========================================================================
class DeletionOut(Out):
    kind: str
    id: UUID


class ChangeOut(Out):
    revision: int
    at: datetime
    kind: str
    entity_id: UUID
    op: str
    command: str
    actor: str
    note: str


class SyncBatchOut(Out):
    """Everything one command changed: the unit a replica applies atomically.

    Typed lists rather than a heterogeneous array, so a client can apply table by
    table and the OpenAPI document actually describes the payload.
    """

    revision: int
    at: datetime
    command: str
    actor: str
    commodities: list[CommodityOut] = Field(default_factory=list)
    accounts: list[AccountOut] = Field(default_factory=list)
    payees: list[PayeeOut] = Field(default_factory=list)
    tags: list[TagOut] = Field(default_factory=list)
    transactions: list[TransactionOut] = Field(default_factory=list)
    prices: list[PriceOut] = Field(default_factory=list)
    schedules: list[ScheduleOut] = Field(default_factory=list)
    book: BookOut | None = None
    deletions: list[DeletionOut] = Field(default_factory=list)
    changes: list[ChangeOut] = Field(
        default_factory=list, description="The row-level audit records."
    )


class SyncPageOut(Out):
    from_revision: int
    to_revision: int = Field(description="The client's new watermark.")
    head: int = Field(description="The book's current revision.")
    complete: bool = Field(description="False means call again from to_revision.")
    batches: list[SyncBatchOut]


# ==========================================================================
# quotes
# ==========================================================================
class QuoteProviderOut(Out):
    name: str = Field(description="What goes in a commodity's `quote_source`.")
    description: str


class QuoteFetchIn(In):
    """Run the quote service now. Everything is optional: the default run asks
    each provider for what it has not given yet."""

    start: date | None = Field(default=None, description="Backfill from this date.")
    end: date | None = Field(default=None, description="Up to this date; default today.")
    commodity_ids: list[UUID] | None = Field(
        default=None, description="Only these commodities."
    )
    providers: list[str] | None = Field(default=None, description="Only these providers.")


class QuoteProblemOut(Out):
    provider: str
    commodity: str
    message: str


class QuoteRunOut(Out):
    """What one run did. `stored` counts new price rows; `unchanged` quotes the
    book already had; `problems` is per provider and commodity."""

    started_at: datetime
    finished_at: datetime | None
    start: date | None
    end: date | None
    requested: int
    up_to_date: int
    fetched: int
    stored: int
    unchanged: int
    ok: bool
    providers: list[str]
    problems: list[QuoteProblemOut]
    summary: str

    @classmethod
    def of(cls, report: Any) -> QuoteRunOut:
        return cls(
            started_at=report.started_at,
            finished_at=report.finished_at,
            start=report.start,
            end=report.end,
            requested=report.requested,
            up_to_date=report.up_to_date,
            fetched=report.fetched,
            stored=report.stored,
            unchanged=report.unchanged,
            ok=report.ok,
            providers=list(report.providers),
            problems=[
                QuoteProblemOut(
                    provider=p.provider, commodity=p.commodity, message=p.message
                )
                for p in report.problems
            ],
            summary=report.summary(),
        )


# ==========================================================================
# auth
# ==========================================================================
class RegisterIn(In):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=1024)
    display_name: str = ""
    locale: Literal["en", "nl"] = Field(
        default="en", description="Language for the account and its emails."
    )


class LoginIn(In):
    email: str
    password: str


class EmailIn(In):
    email: str = Field(min_length=3, max_length=255)


class ActivateIn(In):
    token: str = Field(min_length=8, max_length=256)


class ResetPasswordIn(In):
    token: str = Field(min_length=8, max_length=256)
    password: str = Field(min_length=12, max_length=1024)


class DeleteMeIn(In):
    password: str = Field(description="Your current password, to confirm.")


class PendingOut(Out):
    """Something was (or would have been) emailed; nothing else is revealed."""

    status: Literal["pending"] = "pending"
    email: str


class UserOut(Out):
    id: UUID
    email: str
    display_name: str
    locale: str = Field(
        default="en", description="The language the user chose: 'en' or 'nl'."
    )
    csrf_token: str | None = Field(
        default=None,
        description=(
            "The session's CSRF token, returned by GET /auth/me so a browser tab "
            "that holds the cookie but not the token (a fresh tab, a restored "
            "session) can write again. Bound to the session, useless without the "
            "cookie, and unreadable cross-origin; bearer clients can ignore it."
        ),
    )


class MePatch(In):
    """What a user may change about themselves."""

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    locale: Literal["en", "nl"] | None = None


class SessionOut(Out):
    user: UserOut
    csrf_token: str = Field(
        description=(
            "Send as X-CSRF-Token on unsafe requests that authenticate with the "
            "session cookie. Not needed for bearer-token clients."
        )
    )
    token: str | None = Field(
        default=None,
        description=(
            "Bearer token, for clients that cannot use cookies (desktop, mobile). "
            "Returned once, at login."
        ),
    )
    expires_at: datetime


class GrantIn(In):
    subject: str = Field(description="A user's email, or an OIDC subject claim.")
    role: Literal["viewer", "editor", "owner"]


class GrantOut(Out):
    book_id: UUID
    subject: str
    role: str


class HealthOut(Out):
    status: Literal["ok", "degraded"]
    version: str
    database: str = Field(description="The dialect in use, without credentials.")
    books: int


# ==========================================================================
# scheduled / recurring transactions
# ==========================================================================
FrequencyName = Literal["DAILY", "WEEKLY", "MONTHLY", "YEARLY"]


class RecurrenceModel(In):
    """A recurrence rule: a small, exact subset of RFC 5545."""

    frequency: FrequencyName
    start: PostDate = Field(description="The first occurrence.")
    interval: int = Field(default=1, ge=1, description="Every N units.")
    monthly_by_weekday: bool = Field(
        default=False,
        description="MONTHLY only: the nth weekday of the month (a 'third "
        "Friday') rather than a day number.",
    )
    count: int | None = Field(
        default=None, ge=1, description="End after this many occurrences."
    )
    until: PostDate | None = Field(
        default=None, description="End on or before this date (inclusive)."
    )

    def to_domain(self) -> _Recurrence:
        return _Recurrence(
            frequency=_Frequency(self.frequency),
            start=self.start,
            interval=self.interval,
            monthly_by_weekday=self.monthly_by_weekday,
            count=self.count,
            until=self.until,
        )

    @classmethod
    def of(cls, recurrence: _Recurrence) -> RecurrenceModel:
        return cls.model_validate(recurrence.to_dict())


class TemplateLegIn(In):
    account_id: UUID
    value: Exact | None = None
    quantity: Exact | None = None
    price: RateLike | None = None
    memo: str = ""
    payee_id: UUID | None = None

    def to_domain(self) -> _TemplateLeg:
        return _TemplateLeg(
            account_id=self.account_id,
            value=None if self.value is None else to_fraction(self.value),
            quantity=None if self.quantity is None else to_fraction(self.quantity),
            price=None if self.price is None else to_fraction(self.price),
            memo=self.memo,
            payee_id=self.payee_id,
        )


class TemplateLegOut(Out):
    account_id: UUID
    value: str | None = Field(
        default=None, description="Exact major-unit string,'10/3' form allowed."
    )
    quantity: str | None = None
    price: str | None = None
    memo: str = ""
    payee_id: UUID | None = None
    is_residual: bool

    @classmethod
    def of(cls, leg: _TemplateLeg) -> TemplateLegOut:
        def s(x: Fraction | None) -> str | None:
            return None if x is None else str(x)

        return cls(
            account_id=leg.account_id,
            value=s(leg.value),
            quantity=s(leg.quantity),
            price=s(leg.price),
            memo=leg.memo,
            payee_id=leg.payee_id,
            is_residual=leg.is_residual,
        )


class ScheduleTemplateIn(In):
    legs: list[TemplateLegIn] = Field(min_length=1)
    currency_id: UUID | None = None
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode = RoundingMode.HALF_EVEN
    imbalance_account_id: UUID | None = None
    estimate: bool = Field(
        default=False,
        description="The amounts are a guess (a variable bill); shown as '≈'.",
    )

    def to_domain(self) -> _ScheduleTemplate:
        return _ScheduleTemplate(
            legs=tuple(leg.to_domain() for leg in self.legs),
            currency_id=self.currency_id,
            memo=self.memo,
            payee_id=self.payee_id,
            number=self.number,
            rounding=self.rounding,
            imbalance_account_id=self.imbalance_account_id,
            estimate=self.estimate,
        )


class ScheduleTemplateOut(Out):
    legs: list[TemplateLegOut]
    currency_id: UUID | None = None
    memo: str = ""
    payee_id: UUID | None = None
    number: str = ""
    rounding: RoundingMode
    imbalance_account_id: UUID | None = None
    estimate: bool = False

    @classmethod
    def of(cls, template: _ScheduleTemplate) -> ScheduleTemplateOut:
        return cls(
            legs=[TemplateLegOut.of(leg) for leg in template.legs],
            currency_id=template.currency_id,
            memo=template.memo,
            payee_id=template.payee_id,
            number=template.number,
            rounding=template.rounding,
            imbalance_account_id=template.imbalance_account_id,
            estimate=template.estimate,
        )


class ScheduleIn(In):
    name: str = Field(min_length=1, max_length=255)
    template: ScheduleTemplateIn
    recurrence: RecurrenceModel
    auto_post: bool = Field(
        default=False,
        description="A client may post a due occurrence of this schedule "
        "without a per-occurrence confirmation. The server never posts.",
    )
    paused: bool = False
    id: UUID | None = None


class SchedulePatch(In):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    template: ScheduleTemplateIn | None = None
    recurrence: RecurrenceModel | None = None
    auto_post: bool | None = None
    paused: bool | None = None


class ScheduleOut(Out):
    id: UUID
    name: str
    template: ScheduleTemplateOut
    recurrence: RecurrenceModel
    recurrence_text: str = Field(
        description="The rule in words, e.g. 'every month on day 1'."
    )
    occurrences_done: int
    total_occurrences: int | None = Field(
        default=None, description="Null for an open-ended rule."
    )
    next_due: PostDate | None = Field(
        default=None, description="Null once the schedule has finished."
    )
    last_occurrence: PostDate | None = None
    auto_post: bool
    paused: bool
    finished: bool
    upcoming: list[PostDate] = Field(
        default_factory=list, description="The next few occurrence dates, for a preview."
    )
    revision: int

    @classmethod
    def of(cls, schedule: _Schedule) -> ScheduleOut:
        assert schedule.template is not None and schedule.recurrence is not None
        return cls(
            id=schedule.id,
            name=schedule.name,
            template=ScheduleTemplateOut.of(schedule.template),
            recurrence=RecurrenceModel.of(schedule.recurrence),
            recurrence_text=schedule.recurrence.describe(),
            occurrences_done=schedule.occurrences_done,
            total_occurrences=schedule.total_occurrences,
            next_due=schedule.next_due,
            last_occurrence=schedule.last_occurrence,
            auto_post=schedule.auto_post,
            paused=schedule.paused,
            finished=schedule.is_finished,
            upcoming=list(schedule.upcoming(6)),
            revision=schedule.revision,
        )


class DueScheduleOut(Out):
    """A schedule with at least one occurrence on or before the asked date."""

    schedule: ScheduleOut
    pending: list[PostDate] = Field(
        description="Every occurrence from the cursor up to the date, oldest first."
    )
    amount: AmountOut | None = Field(
        default=None,
        description="What one occurrence would post to the first non-residual "
        "leg, for a quick total. Null if it cannot be worked out without the "
        "book (an estimate leg, a price needed).",
    )

    @classmethod
    def of(cls, schedule: _Schedule, pending: tuple[Any, ...], amount: Amount | None):
        return cls(
            schedule=ScheduleOut.of(schedule),
            pending=list(pending),
            amount=None if amount is None else AmountOut.of(amount),
        )


class DuePanelOut(Out):
    as_of: PostDate
    due: list[DueScheduleOut]
    occurrences: int = Field(
        description="Total pending occurrences across all schedules."
    )


class PostOccurrenceIn(In):
    """Confirm (and optionally adjust) the occurrence a schedule has due."""

    on: PostDate | None = Field(
        default=None, description="Pin which occurrence; must be the one due."
    )
    legs: list[LegIn] | None = Field(
        default=None, description="Replace the template's legs for this occurrence only."
    )
    memo: str | None = None
    payee_id: UUID | None = None
    number: str | None = None
    transaction_id: UUID | None = Field(
        default=None,
        description="Id for the posted transaction; set by an offline client "
        "so its replica row and the server's are one on replay.",
    )


class SkipOccurrenceIn(In):
    on: PostDate | None = Field(
        default=None, description="Pin which occurrence; must be the one due."
    )


# ==========================================================================
# importing a bank statement (CSV)
# ==========================================================================
# The wire mirror of :mod:`pymoney.importers.statement`. A preview and a commit
# are deliberately *stateless*: the file is uploaded each time and the server
# re-derives everything from it, so there is no half-built import to keep alive
# between two requests, and the reviewer's edits can only touch the soft fields
# (payee, category, skip) -- never the date or the amount, which are the bank's.
AmountKindName = Literal["signed", "debit_credit"]
ProposalStatusName = Literal["new", "duplicate", "transfer"]
RowActionName = Literal["post", "skip"]


class ColumnMappingModel(In):
    """Which CSV column is which, and how its values are written.

    Built once per account (the server suggests a starting point) and then
    remembered, so the next download from the same bank opens ready to review.
    """

    date: str = Field(description="The column holding each row's posting date.")
    date_format: str = Field(
        default="",
        description=(
            "A strptime pattern (e.g. '%d/%m/%Y'). Empty: detect it from the file."
        ),
    )
    amount_kind: AmountKindName = Field(
        default="signed",
        description=(
            "'signed': one column, a minus for money out. 'debit_credit': a "
            "money-out column and a money-in column, each a positive number."
        ),
    )
    amount: str = Field(default="", description="The signed-amount column.")
    debit: str = Field(default="", description="The money-out column.")
    credit: str = Field(default="", description="The money-in column.")
    decimal: Literal[".", ","] = Field(
        default=".", description="The decimal separator used in the amounts."
    )
    invert: bool = Field(
        default=False,
        description="Flip every sign, for a bank that lists a withdrawal as positive.",
    )
    description: list[str] = Field(
        default_factory=list,
        description=(
            "Columns joined into the text the payee matcher reads. Falls back "
            "to `memo` when empty, so one text column need only be mapped once."
        ),
    )
    reference: str = Field(
        default="",
        description=(
            "The bank's own unique id for the line, if it has one. With it, "
            "duplicate detection is exact across overlapping downloads."
        ),
    )
    memo: list[str] = Field(
        default_factory=list,
        description=(
            "Columns joined into the transaction's memo. Falls back to "
            "`description` when empty, so every posted row keeps the bank's "
            "own wording."
        ),
    )
    skip_rows: int = Field(
        default=0, ge=0, description="Preamble lines above the header."
    )
    has_header: bool = True
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = "utf-8"

    def to_domain(self) -> _ColumnMapping:
        return _ColumnMapping(
            date=self.date,
            date_format=self.date_format,
            amount_kind=_AmountKind(self.amount_kind),
            amount=self.amount,
            debit=self.debit,
            credit=self.credit,
            decimal=self.decimal,
            invert=self.invert,
            description=tuple(self.description),
            reference=self.reference,
            memo=tuple(self.memo),
            skip_rows=self.skip_rows,
            has_header=self.has_header,
            delimiter=self.delimiter,
            encoding=self.encoding,
        )

    @classmethod
    def of(cls, mapping: _ColumnMapping) -> ColumnMappingModel:
        return cls.model_validate(mapping.to_dict())


class CsvInspectOut(Out):
    """What one upload looks like, before any mapping is applied."""

    columns: list[str] = Field(
        description="Header names, or '1', '2', ... for a file with no header."
    )
    sample_rows: list[dict[str, str]] = Field(
        description="The first rows, column name to cell, for the mapping screen."
    )
    row_count: int
    encoding: str
    delimiter: str
    has_header: bool
    skip_rows: int = Field(description="Preamble lines the sniffer skipped.")
    suggested_mapping: ColumnMappingModel = Field(
        description="A starting point, every field of it overridable."
    )


class ImportProposalOut(Out):
    """One statement row and what the importer proposes to do with it."""

    row_key: str = Field(
        description=(
            "The row's stable id -- its deduplication key. Pass it back in a "
            "decision, and to undo the import later (it becomes the "
            "transaction's imported_id)."
        )
    )
    index: int = Field(description="Position in the file, zero-based.")
    date: PostDate
    amount: AmountOut = Field(description="Signed; positive is money in.")
    description: str
    reference: str
    memo: str
    status: ProposalStatusName = Field(
        description=(
            "'new': post it. 'duplicate': already in the book. 'transfer': the "
            "other side is already recorded on another of your accounts."
        )
    )
    default_action: RowActionName = Field(
        description="'skip' for a duplicate or a recorded transfer, else 'post'."
    )
    existing_transaction_id: UUID | None = Field(
        default=None, description="The duplicate, or the far side of the transfer."
    )
    existing_account_id: UUID | None = Field(
        default=None, description="For a transfer: the other account."
    )
    existing_account_path: str = ""
    suggested_payee_id: UUID | None = None
    suggested_payee_name: str = Field(
        default="",
        description=(
            "The matched payee's name, or -- with no suggested_payee_id -- a "
            "cleaned-up name to offer as a new payee."
        ),
    )
    suggested_category_id: UUID | None = None
    suggestion_reason: str = Field(
        description="'match key', 'payee default', 'last used', 'transfer', "
        "'new payee', or '' when the importer had nothing to go on."
    )

    @classmethod
    def of(cls, proposal: Any, commodity: Commodity) -> ImportProposalOut:
        entry = proposal.entry
        return cls(
            row_key=entry.imported_id,
            index=entry.index,
            date=entry.date,
            amount=AmountOut.of(Amount.from_major(commodity, entry.amount)),
            description=entry.description,
            reference=entry.reference,
            memo=entry.memo,
            status=proposal.status.value,
            default_action="skip" if proposal.default_skip else "post",
            existing_transaction_id=proposal.existing_transaction_id,
            existing_account_id=proposal.existing_account_id,
            existing_account_path=proposal.existing_account_path,
            suggested_payee_id=proposal.suggestion.payee_id,
            suggested_payee_name=proposal.suggestion.payee_name,
            suggested_category_id=proposal.suggestion.category_id,
            suggestion_reason=proposal.suggestion.reason,
        )


class CsvPreviewOut(Out):
    account_id: UUID
    mapping: ColumnMappingModel = Field(
        description="The mapping used; echoed so the review screen can save it."
    )
    proposals: list[ImportProposalOut]


class ImportDecisionIn(In):
    """The reviewer's verdict on one row, keyed by its ``row_key``.

    Only the soft fields: a wrong date or amount is fixed by editing the
    transaction in the register after it is posted, not here.
    """

    row_key: str
    action: RowActionName = "post"
    payee_id: UUID | None = Field(
        default=None, description="An existing payee for the transaction."
    )
    new_payee: str = Field(
        default="",
        max_length=255,
        description=(
            "Create (or reuse) a payee with this name when payee_id is unset. "
            "Rows that name the same payee share the one that gets created."
        ),
    )
    category_id: UUID | None = Field(
        default=None,
        description=(
            "The account the other leg posts to. Unset: the row goes to the "
            "commit's uncategorised_account_id and is flagged for follow-up."
        ),
    )
    memo: str = Field(
        default="", max_length=500, description="Replaces the mapped memo, if set."
    )

    def to_domain(self) -> _RowDecision:
        return _RowDecision(
            row_key=self.row_key,
            action=_RowAction(self.action),
            payee_id=self.payee_id,
            new_payee=self.new_payee,
            category_id=self.category_id,
            memo=self.memo,
        )


class StatementImportOut(Out):
    """What a commit did: posted, skipped, and could not post."""

    account_id: UUID
    account_path: str
    rows: int = Field(description="Lines read from the file.")
    posted: int
    skipped_duplicates: int
    skipped_transfers: int
    skipped_by_user: int
    payees_created: int
    uncategorised: int = Field(
        description="Posted with no category -- in the register, awaiting one."
    )
    revision_before: int
    revision_after: int
    posted_transaction_ids: list[UUID]
    ok: bool = Field(description="No row failed to post.")
    problems: list[ImportProblemOut]
    summary: str

    @classmethod
    def of(cls, report: Any) -> StatementImportOut:
        return cls(
            account_id=report.account_id,
            account_path=report.account_path,
            rows=report.rows,
            posted=report.posted,
            skipped_duplicates=report.skipped_duplicates,
            skipped_transfers=report.skipped_transfers,
            skipped_by_user=report.skipped_by_user,
            payees_created=report.payees_created,
            uncategorised=report.uncategorised,
            revision_before=report.revision_before,
            revision_after=report.revision_after,
            posted_transaction_ids=list(report.posted_ids),
            ok=report.ok,
            problems=[
                ImportProblemOut(
                    level=p.level.name,
                    category=p.category,
                    message=p.message,
                    source_id=p.source_id,
                )
                for p in report.problems
            ],
            summary=report.summary(),
        )

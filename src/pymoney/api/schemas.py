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
from ..core.rounding import RoundingMode
from ..core.settings import BookSettings
from ..core.transaction import ReconcileState, Split, Transaction

__all__ = [
    "AccountIn",
    "AccountOut",
    "ActivateIn",
    "AmountOut",
    "BookIn",
    "BookOut",
    "ChangeOut",
    "CommodityIn",
    "CommodityOut",
    "CommodityPatch",
    "DeleteBookIn",
    "DeleteMeIn",
    "EmailIn",
    "Exact",
    "ImportOut",
    "ImportProblemOut",
    "LegIn",
    "LoginIn",
    "MergeAccountsIn",
    "MergePayeesIn",
    "MoveAccountIn",
    "Page",
    "PayeeIn",
    "PayeeOut",
    "PendingOut",
    "PriceIn",
    "PriceOut",
    "PricePage",
    "QuoteFetchIn",
    "QuoteProviderOut",
    "QuoteRunOut",
    "RateOut",
    "ReconcileIn",
    "ReconcileStateName",
    "RegisterRowOut",
    "RegisterTotals",
    "RenameIn",
    "ResetPasswordIn",
    "SessionOut",
    "SplitOut",
    "SyncBatchOut",
    "SyncPageOut",
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


Exact = Annotated[
    Decimal | int | str,
    BeforeValidator(_reject_float),
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
    BeforeValidator(_reject_float),
    Field(
        examples=["0.9174", "10/9"],
        description="An exact positive rate. A decimal string or a 'num/den' ratio.",
    ),
]


def to_fraction(value: Decimal | int | str) -> Fraction:
    """Wire value to exact rational. ``"10/3"`` and ``"3.33"`` both work."""
    if isinstance(value, Decimal):
        return Fraction(value)
    return Fraction(str(value).strip())


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
            revision=transaction.revision,
        )


class TransactionPage(Page):
    items: list[TransactionOut]


class ReconcileIn(In):
    split_ids: list[UUID] = Field(default_factory=list)
    state: ReconcileStateName = ReconcileStateName.RECONCILED
    statement_date: date | None = None


class SetSplitStateIn(In):
    state: ReconcileStateName
    on: date | None = None


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
            base=price.base.code,
            quote_id=price.quote.id,
            quote=price.quote.code,
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
    transactions: list[TransactionOut] = Field(default_factory=list)
    prices: list[PriceOut] = Field(default_factory=list)
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

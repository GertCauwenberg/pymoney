"""Form logic for the transaction dialog, without Qt: the Python twin of the
web frontend's ``ledger.ts``.

Amounts are the strings the user typed. They are parsed into exact minor units
to show the running total and to catch what the server would refuse anyway;
what is *sent* is the text itself, so nothing is ever a float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from uuid import UUID

from pymoney.api.schemas import (
    AccountOut,
    CommodityOut,
    LegIn,
    TransactionIn,
    TransactionOut,
    TransactionUpdateIn,
)

from .i18n import _

__all__ = [
    "LegDraft",
    "TransactionDraft",
    "draft_from_transaction",
    "parse_decimal",
    "problems",
]

_DECIMAL = re.compile(r"^(-?)(\d*)(?:[.,](\d*))?$")


def parse_decimal(text: str, factor: int) -> int | None:
    """Typed text to minor units, exactly; None if it is not an amount the
    commodity can book (too many decimals is the server's call, not ours)."""
    m = _DECIMAL.match(text.strip())
    if not m:
        return None
    sign, whole, frac = m.group(1), m.group(2) or "", m.group(3) or ""
    if not whole and not frac:
        return None
    value = Fraction(int(whole or "0")) + (
        Fraction(int(frac), 10 ** len(frac)) if frac else 0
    )
    minor = value * factor
    if minor.denominator != 1:
        return None
    return -minor.numerator if sign else minor.numerator


def minor_to_text(minor: int, factor: int) -> str:
    digits = len(str(factor)) - 1 if factor == 10 ** (len(str(factor)) - 1) else 0
    sign = "-" if minor < 0 else ""
    s = str(abs(minor)).rjust(digits + 1, "0")
    return f"{sign}{s[:-digits]}.{s[-digits:]}" if digits else f"{sign}{s}"


@dataclass
class LegDraft:
    account_id: UUID | None = None
    value: str = ""
    quantity: str = ""
    memo: str = ""
    keep: dict = field(default_factory=dict)
    """Reconciliation mark, action, number, per-split payee: kept, not edited."""

    def to_wire(self, accounts: dict[UUID, AccountOut], currency_id: UUID) -> LegIn:
        foreign = (
            self.account_id in accounts
            and accounts[self.account_id].commodity_id != currency_id
        )
        return LegIn.model_validate(
            {
                "account_id": self.account_id,
                "value": self.value.strip().replace(",", ".") or None,
                "quantity": (self.quantity.strip().replace(",", ".") or None)
                if foreign
                else None,
                "memo": self.memo.strip(),
                **self.keep,
            }
        )


@dataclass
class TransactionDraft:
    date: str
    payee_id: UUID | None = None
    new_payee: str = ""
    memo: str = ""
    number: str = ""
    legs: list[LegDraft] = field(default_factory=list)

    def to_wire(
        self,
        accounts: dict[UUID, AccountOut],
        currency: CommodityOut,
        *,
        id: UUID | None = None,
    ) -> TransactionIn:
        return TransactionIn.model_validate(
            {
                "date": self.date,
                "legs": [
                    leg.to_wire(accounts, currency.id).model_dump(mode="json")
                    for leg in self.legs
                ],
                "currency_id": currency.id,
                "memo": self.memo.strip(),
                "payee_id": self.payee_id,
                "number": self.number.strip(),
                "id": id,
            }
        )

    def to_update(
        self, accounts: dict[UUID, AccountOut], currency: CommodityOut
    ) -> TransactionUpdateIn:
        wire = self.to_wire(accounts, currency)
        return TransactionUpdateIn(
            date=wire.date,
            legs=wire.legs,
            memo=wire.memo,
            payee_id=wire.payee_id,
            number=wire.number,
        )


def draft_from_transaction(tx: TransactionOut) -> TransactionDraft:
    return TransactionDraft(
        date=tx.date.isoformat(),
        payee_id=tx.payee_id,
        memo=tx.memo,
        number=tx.number,
        legs=[
            LegDraft(
                account_id=s.account_id,
                value=minor_to_text(s.value.minor, s.value.scale),
                quantity=""
                if s.quantity.commodity_id == tx.currency_id
                else minor_to_text(s.quantity.minor, s.quantity.scale),
                memo=s.memo,
                keep={
                    "reconcile_state": s.reconcile_state.value,
                    "reconcile_date": s.reconcile_date.isoformat()
                    if s.reconcile_date
                    else None,
                    "action": s.action,
                    "number": s.number,
                    "payee_id": s.payee_id,
                },
            )
            for s in tx.splits
        ],
    )


def problems(
    draft: TransactionDraft, accounts: dict[UUID, AccountOut], currency: CommodityOut
) -> list[str]:
    """What stops the draft being sent; the server still has the final say."""
    out: list[str] = []
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", draft.date):
        out.append(_("A date is needed."))
    if len(draft.legs) < 2:
        out.append(_("A transaction needs at least two legs."))
    blanks = 0
    for i, leg in enumerate(draft.legs, start=1):
        if leg.account_id is None:
            out.append(_("Leg {n}: choose an account.", n=i))
            continue
        account = accounts.get(leg.account_id)
        if account is None:
            out.append(_("Leg {n}: unknown account.", n=i))
            continue
        if account.placeholder:
            out.append(
                _(
                    "Leg {n}: {path} is a placeholder and takes no postings.",
                    n=i,
                    path=account.path,
                )
            )
        if account.closed:
            out.append(_("Leg {n}: {path} is closed.", n=i, path=account.path))
        if not leg.value.strip():
            blanks += 1
        elif parse_decimal(leg.value, currency.minor_unit_factor) is None:
            out.append(_("Leg {n}: not an amount in {code}.", n=i, code=currency.code))
        # A leg in another commodity may carry its quantity, but need not: left
        # blank, the server values it from the book's price history; filled in,
        # the value over the quantity becomes a "transaction" price. The server
        # has the last word on what that commodity can book.
    if blanks > 1:
        out.append(_("Only one leg may be left blank to balance the entry."))
    return out


def balance(draft: TransactionDraft, currency: CommodityOut) -> tuple[int, bool]:
    """Sum of the filled-in values in minor units, and whether a leg is blank."""
    total, blank = 0, False
    for leg in draft.legs:
        if not leg.value.strip():
            blank = True
            continue
        minor = parse_decimal(leg.value, currency.minor_unit_factor)
        if minor is not None:
            total += minor
    return total, blank

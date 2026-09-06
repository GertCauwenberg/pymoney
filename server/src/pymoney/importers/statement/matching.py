"""Guessing a payee and a category from a statement line's description.

The order is the one the domain was built for (see
:class:`~pymoney.core.payee.Payee`):

1. **The payee** is whichever one has a :attr:`~pymoney.core.payee.Payee.match_keys`
   entry -- or a name -- that appears in the description. The most specific
   match wins, so "AMValentino ES" finds *Amazon ES* over a bare *Amazon*.
2. **The category** is that payee's
   :attr:`~pymoney.core.payee.Payee.default_account_id` if it has one; failing
   that, the category the payee's most recent transaction in this account was
   filed under. A household files the same shop the same way, so "last time"
   is a good default and a one-click correction when it is wrong.

When nothing matches, the description is cleaned into a proposed *new* payee
name -- the transaction-type noise and trailing reference stripped -- so
adopting it is one click rather than a round of copy-and-paste.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from ...core.account import Account
from ...core.errors import NotFoundError
from ...core.payee import Payee

if TYPE_CHECKING:  # pragma: no cover
    from ...core.book import Book

__all__ = ["Matcher", "Suggestion", "clean_payee_name", "suggest_for"]

_NON_WORD = re.compile(r"[^0-9a-z]+")
# Tokens a card network prepends; never part of the merchant's name.
_LEADING_NOISE = frozenset(
    {
        "pos",
        "atm",
        "card",
        "crd",
        "debit",
        "credit",
        "payment",
        "pmt",
        "purchase",
        "dd",
        "sdd",
        "sepa",
        "so",
        "sto",
        "trf",
        "transfer",
        "tfr",
        "bgc",
        "fpi",
        "fps",
        "dr",
        "cr",
        "ref",
        "chk",
        "cheque",
        "check",
        "withdrawal",
        "deposit",
        "online",
        "contactless",
        "clp",
        "vis",
        "visa",
        "mc",
        "mastercard",
        "ideal",
        "incasso",
        "overschrijving",
        "betaalautomaat",
        "geldautomaat",
    }
)
_DATE_LIKE = re.compile(r"^\d{1,4}[-/.]\d{1,2}([-/.]\d{1,4})?$|^\d{6,8}$")
_TIME_LIKE = re.compile(r"^\d{1,2}[:.]\d{2}([:.]\d{2})?$")
_AMOUNT_LIKE = re.compile(r"^[-+(]?\d[\d.,']*\)?$")
_COUNTRY = re.compile(r"^[A-Z]{2,3}$")


@dataclass(frozen=True, slots=True, kw_only=True)
class Suggestion:
    """What the importer proposes for one row, before the user reviews it."""

    payee_id: UUID | None = None
    payee_name: str = ""
    """The matched payee's name, or -- when nothing matched -- a cleaned-up
    name to offer as a new payee. ``payee_id is None and payee_name`` means
    "create this one"."""
    category_id: UUID | None = None
    reason: str = ""
    """How the guess was reached: ``"match key"``, ``"payee default"``,
    ``"last used"``, ``"new payee"``, or ``""`` for nothing."""

    @property
    def is_new_payee(self) -> bool:
        return self.payee_id is None and bool(self.payee_name)


def _normalise(text: str) -> str:
    return _NON_WORD.sub(" ", text.casefold()).strip()


def _is_noise_token(token: str) -> bool:
    return bool(
        _DATE_LIKE.match(token)
        or _TIME_LIKE.match(token)
        or _AMOUNT_LIKE.match(token)
        or _COUNTRY.match(token)
        or token.isdigit()
    )


def clean_payee_name(description: str) -> str:
    """A plausible merchant name out of a raw statement line.

    Drops the transaction-type words a card network prepends and the date,
    amount, reference and country code it appends, keeps the middle, and
    tames an ALL-CAPS line into Title Case. Good enough to adopt with one
    click and rename later if the bank was unusually cryptic.
    """
    raw = re.sub(r"\s+", " ", description.replace("*", " ").replace("/", " ")).strip()
    if not raw:
        return ""
    tokens = raw.split(" ")
    while tokens and (
        _normalise(tokens[0]).replace(" ", "") in _LEADING_NOISE
        or _is_noise_token(tokens[0])
    ):
        tokens.pop(0)
    while tokens and _is_noise_token(tokens[-1]):
        tokens.pop()
    name = " ".join(tokens).strip(" -,.#").strip()
    if not name:
        name = raw
    if name == name.upper():
        name = name.title()
    return name[:120]


class Matcher:
    """Payee and category suggestions for an import into one account.

    Built once per import: it indexes the book's payees by match key and
    caches each payee's last-used category, so a statement with two hundred
    lines from the same four shops does not walk their histories two hundred
    times.
    """

    def __init__(self, book: Book, account: Account) -> None:
        self._book = book
        self._account = account
        # (normalised key, payee, specificity). Longest key first, so the most
        # specific match is the one found.
        keyed: list[tuple[str, Payee, int]] = []
        for payee in book.payees():
            for raw in (payee.name, *payee.match_keys):
                key = _normalise(raw)
                if key:
                    keyed.append((key, payee, len(key)))
        keyed.sort(key=lambda item: (-item[2], item[1].name))
        self._keys = keyed
        self._last_category: dict[UUID, UUID | None] = {}

    def suggest(self, description: str) -> Suggestion:
        payee = self._match_payee(description)
        if payee is None:
            return Suggestion(
                payee_name=clean_payee_name(description),
                reason="new payee" if description.strip() else "",
            )
        if payee.default_account_id is not None and self._account_ok(
            payee.default_account_id
        ):
            return Suggestion(
                payee_id=payee.id,
                payee_name=payee.name,
                category_id=payee.default_account_id,
                reason="payee default",
            )
        last = self._last_used_category(payee.id)
        return Suggestion(
            payee_id=payee.id,
            payee_name=payee.name,
            category_id=last,
            reason="last used" if last is not None else "match key",
        )

    # -- internals -----------------------------------------------------------
    def _match_payee(self, description: str) -> Payee | None:
        haystack = f" {_normalise(description)} "
        if haystack.strip():
            for key, payee, _ in self._keys:
                if f" {key} " in haystack or haystack.strip() == key:
                    return payee
        return None

    def _account_ok(self, account_id: UUID) -> bool:
        """A category must be an account that still exists and takes postings."""
        try:
            account = self._book.account(account_id)
        except NotFoundError:
            return False
        return not account.placeholder

    def _last_used_category(self, payee_id: UUID) -> UUID | None:
        if payee_id in self._last_category:
            return self._last_category[payee_id]
        chosen: UUID | None = None
        transactions = sorted(
            self._book.store.transactions_for_payee(payee_id),
            key=lambda t: (t.date, t.id.int),
            reverse=True,
        )
        for transaction in transactions:
            touches_account = any(
                s.account_id == self._account.id for s in transaction.splits
            )
            if not touches_account:
                continue
            others = [
                s.account_id
                for s in transaction.splits
                if s.account_id != self._account.id
            ]
            # A two-legged transaction names its category outright; a split
            # one is ambiguous, so leave it to the user.
            if len(set(others)) == 1 and self._account_ok(others[0]):
                chosen = others[0]
                break
        self._last_category[payee_id] = chosen
        return chosen


def suggest_for(book: Book, account: Account, description: str) -> Suggestion:
    """One-off suggestion; for a whole statement, reuse a :class:`Matcher`."""
    return Matcher(book, account).suggest(description)

"""Entries plus the book: what to do with each row, for the review screen.

:func:`propose` is read-only. It takes the parsed statement and the book it is
headed for and returns one :class:`Proposal` per row -- a status, a suggested
payee and category, and, where it found one, the transaction the row is a
duplicate of or the far side of. Nothing is written; the user reviews these,
edits what the heuristics got wrong, and the edited set is what
:func:`~pymoney.importers.statement.commit.import_statement` posts.

Three statuses:

``NEW``
    Not seen before. Post it.
``DUPLICATE``
    Already in the book: a transaction with this row's ``imported_id``, or --
    for a row imported before dedup keys existed, or typed in by hand -- a
    transaction in *this* account with the same amount on the same day.
    Default: skip.
``TRANSFER``
    The mirror image is recorded against another of your own cash accounts
    (same amount, opposite sign, within a few days), but not yet against this
    one. Posting it would count the money twice, so the default is again to
    skip; the proposal names the other transaction so a reviewer who disagrees
    can go and look.

Each existing transaction is matched at most once, so re-importing a week that
contained two identical £3.50 coffees flags two duplicates, not every coffee
you have ever bought.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from ...core.account import Account, AccountClass
from ...core.rounding import round_fraction
from ...core.transaction import Transaction
from .entry import StatementEntry
from .matching import Matcher, Suggestion

if TYPE_CHECKING:  # pragma: no cover
    from ...core.book import Book

__all__ = ["Proposal", "ProposalStatus", "propose"]

MATCH_WINDOW = timedelta(days=4)
"""How far apart a statement row and the transaction it matches may be dated.
The two banks either side of a transfer rarely post on the same day; four days
covers a weekend. A same-account duplicate is usually exact, but a pending
card authorisation that posted a day later is not."""


class ProposalStatus(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    TRANSFER = "transfer"


@dataclass(frozen=True, slots=True, kw_only=True)
class Proposal:
    """One statement row, with everything the review screen needs about it."""

    entry: StatementEntry
    status: ProposalStatus
    suggestion: Suggestion
    existing_transaction_id: UUID | None = None
    """The duplicate, or the far side of the transfer."""
    existing_account_id: UUID | None = None
    """For a transfer: the account the money moved to or from."""
    existing_account_path: str = ""

    @property
    def default_skip(self) -> bool:
        """A duplicate or an already-recorded transfer is skipped unless the
        reviewer says otherwise; a new row is posted."""
        return self.status is not ProposalStatus.NEW


def propose(
    book: Book, account: Account, entries: list[StatementEntry]
) -> list[Proposal]:
    """Classify and annotate every entry. Read-only; safe on preview."""
    by_imported_id = {
        entry.imported_id: book.store.find_imported(entry.imported_id)
        for entry in entries
    }
    index = _ExistingIndex(book, account, entries)
    matcher = Matcher(book, account)

    proposals: list[Proposal] = []
    for entry in entries:
        exact = by_imported_id.get(entry.imported_id)
        if exact is not None:
            proposals.append(
                Proposal(
                    entry=entry,
                    status=ProposalStatus.DUPLICATE,
                    suggestion=Suggestion(reason="already imported"),
                    existing_transaction_id=exact,
                )
            )
            continue

        match = index.take(entry)
        if match is not None and match.other_account_id is None:
            proposals.append(
                Proposal(
                    entry=entry,
                    status=ProposalStatus.DUPLICATE,
                    suggestion=matcher.suggest(entry.description),
                    existing_transaction_id=match.transaction_id,
                )
            )
            continue
        if match is not None and match.other_account_id is not None:
            other = book.account(match.other_account_id)
            proposals.append(
                Proposal(
                    entry=entry,
                    status=ProposalStatus.TRANSFER,
                    suggestion=Suggestion(
                        category_id=other.id,
                        payee_name=other.name,
                        reason="transfer",
                    ),
                    existing_transaction_id=match.transaction_id,
                    existing_account_id=other.id,
                    existing_account_path=book.path(other),
                )
            )
            continue

        proposals.append(
            Proposal(
                entry=entry,
                status=ProposalStatus.NEW,
                suggestion=matcher.suggest(entry.description),
            )
        )
    return proposals


@dataclass(frozen=True, slots=True, kw_only=True)
class _Match:
    transaction_id: UUID
    other_account_id: UUID | None = None
    """The far cash account, for a transfer; ``None`` when the match is a
    transaction already booked against the account being imported into."""


class _ExistingIndex:
    """Transactions already in the book that a statement row might be.

    Built once over the statement's date range (widened by
    :data:`MATCH_WINDOW`), covering the account being imported into and every
    other asset or liability account. A row is looked up by the booked amount
    it would post; the closest-dated unclaimed match wins, and a match is
    consumed so two identical rows cannot both claim one transaction.
    """

    def __init__(
        self, book: Book, account: Account, entries: list[StatementEntry]
    ) -> None:
        self._account = account
        self._claimed: set[UUID] = set()
        # booked minor units in the import account's commodity -> matches.
        self._same: dict[int, list[tuple[date, _Match]]] = {}
        self._transfer: dict[int, list[tuple[date, _Match]]] = {}
        if not entries:
            return

        commodity = account.commodity
        cash = {
            other.id: other
            for other in book.accounts(include_closed=True)
            if other.id != account.id
            and other.account_class in (AccountClass.ASSET, AccountClass.LIABILITY)
            and not other.placeholder
            and other.commodity.id == commodity.id
        }
        start = min(e.date for e in entries) - MATCH_WINDOW
        end = max(e.date for e in entries) + MATCH_WINDOW
        for transaction in book.store.transactions(
            account_ids=[account.id, *cash], start=start, end=end
        ):
            self._index(transaction, account, cash)

    def _index(
        self,
        transaction: Transaction,
        account: Account,
        cash: dict[UUID, Account],
    ) -> None:
        touches_account = any(s.account_id == account.id for s in transaction.splits)
        for split in transaction.splits:
            if split.quantity.is_zero:
                continue
            if split.account_id == account.id:
                self._same.setdefault(split.quantity.minor, []).append(
                    (
                        transaction.date,
                        _Match(transaction_id=transaction.id),
                    )
                )
            elif not touches_account and split.account_id in cash:
                # The far leg, negated: a statement row looks *itself* up.
                self._transfer.setdefault(-split.quantity.minor, []).append(
                    (
                        transaction.date,
                        _Match(
                            transaction_id=transaction.id,
                            other_account_id=split.account_id,
                        ),
                    )
                )

    def take(self, entry: StatementEntry) -> _Match | None:
        minor = round_fraction(entry.amount * self._account.commodity.minor_unit_factor)
        # An exact same-account recording beats an inferred transfer.
        return self._closest(self._same, minor, entry.date) or self._closest(
            self._transfer, minor, entry.date
        )

    def _closest(
        self,
        index: dict[int, list[tuple[date, _Match]]],
        minor: int,
        on: date,
    ) -> _Match | None:
        within = sorted(
            (
                (abs((when - on).days), match)
                for when, match in index.get(minor, ())
                if match.transaction_id not in self._claimed
                and abs((when - on).days) <= MATCH_WINDOW.days
            ),
            key=lambda pair: pair[0],
        )
        if not within:
            return None
        match = within[0][1]
        self._claimed.add(match.transaction_id)
        return match

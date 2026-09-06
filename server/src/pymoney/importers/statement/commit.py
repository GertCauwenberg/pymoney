"""The reviewed statement, posted.

:func:`import_statement` takes the parsed entries and the reviewer's decision
for each one and writes them to the book inside a single
:meth:`~pymoney.core.book.Book.bulk` unit of work: all-or-nothing against the
database, one revision per transaction, one new payee created however many
rows asked for it.

It re-classifies the entries first (the same :func:`~.proposals.propose` the
preview showed), because the book may have moved on since the user opened the
review screen -- the far side of a transfer posted from the other account, the
very same file imported again in another tab. A row the reviewer marked to
post that is now a duplicate is skipped and said so; their edits to payee and
category are honoured, the server's view of what already exists is not
overridden.

One malformed row does not sink the import: the per-command savepoint inside
``bulk()`` rolls back just that transaction, it lands in the report, and the
rest go in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from fractions import Fraction
from typing import TYPE_CHECKING
from uuid import UUID

from ...core.account import Account
from ...core.commodity import Commodity
from ...core.errors import NoPriceError, PyMoneyError, ValidationError
from ...core.posting import LegSpec
from ...core.price import ValuationPolicy
from .entry import StatementEntry
from .proposals import ProposalStatus, propose
from .report import StatementReport

if TYPE_CHECKING:  # pragma: no cover
    from ...core.book import Book

__all__ = ["RowAction", "RowDecision", "import_statement"]

_NUMBER_MAX = 64


class RowAction(StrEnum):
    POST = "post"
    SKIP = "skip"


@dataclass(frozen=True, slots=True, kw_only=True)
class RowDecision:
    """What the reviewer decided about one row, keyed by its ``imported_id``.

    A decision overrides only the soft fields. The date and the amount come
    from the bank and are not the reviewer's to edit here; to change those,
    edit the transaction in the register after it is posted.
    """

    row_key: str
    action: RowAction = RowAction.POST
    payee_id: UUID | None = None
    new_payee: str = ""
    """Create (or reuse) a payee with this name when ``payee_id`` is unset."""
    category_id: UUID | None = None
    memo: str = ""
    """Replaces the memo the mapping produced, when non-empty."""


@dataclass(slots=True)
class _PayeePool:
    """Find-or-create, so N rows naming the same new payee make one payee."""

    book: Book
    created: int = 0
    _by_name: dict[str, UUID] = field(default_factory=dict)

    def resolve(self, name: str) -> UUID:
        key = name.strip().casefold()
        if key in self._by_name:
            return self._by_name[key]
        existing = self.book.find_payee(name)
        if existing is not None:
            self._by_name[key] = existing.id
            return existing.id
        payee = self.book.add_payee(name.strip())
        self.created += 1
        self._by_name[key] = payee.id
        return payee.id


@dataclass(slots=True)
class _Rates:
    """The rate each row needs to cross from a category's commodity into the
    account's, looked up once per (pair, date) over the book's prices."""

    book: Book
    into: Commodity
    _cache: dict[tuple[UUID, date], Fraction | None] = field(default_factory=dict)

    def of(self, category_commodity: Commodity, on: date) -> Fraction:
        """``category`` units per ``account`` unit, on or before ``on``.

        The value a residual leg in ``category``'s commodity needs: the
        booked account amount is in ``into``, and ``quantity = value / rate``.

        :raises NoPriceError: naming the pair and the date, if the book holds
            no usable rate -- exactly what to add under Prices and re-import.
        """
        if category_commodity.id == self.into.id:
            raise AssertionError("a same-commodity leg needs no rate")
        key = (category_commodity.id, on)
        if key not in self._cache:
            policy = ValuationPolicy(target=self.into, as_of=on)
            try:
                self._cache[key] = self.book.prices.rate(
                    category_commodity, self.into, policy
                )
            except NoPriceError:
                self._cache[key] = None
        rate = self._cache[key]
        if rate is None:
            raise NoPriceError(
                f"no {category_commodity.code}/{self.into.code} price on or "
                f"before {on} (either direction is fine); add one under Prices "
                f"and import the file again"
            )
        return rate


def import_statement(
    book: Book,
    account: Account,
    entries: list[StatementEntry],
    decisions: dict[str, RowDecision],
    *,
    uncategorised_account_id: UUID | None = None,
    actor: str = "",
) -> StatementReport:
    """Post the reviewed statement into ``account``.

    :param decisions: by ``imported_id``. A row with no decision takes the
        proposal's default -- a new row is posted, a duplicate or an
        already-recorded transfer is skipped.
    :param uncategorised_account_id: where the residual of a posted row with
        no chosen category goes. Required if any such row is to be posted;
        those transactions are counted as ``uncategorised`` in the report so
        the household can finish them off in the register.

    Every transaction is denominated in ``account``'s own commodity -- a bank
    reports in the account's currency, never the book's. When a category is
    held in a different commodity (a EUR grocery account paid from a HUF
    current account), the transaction is a genuine conversion: the rate comes
    from the book's prices for the statement date, either stored direction,
    triangulated if need be. A row for which no rate exists is reported, not
    fatal -- add the price and import the file again.
    """
    report = StatementReport(
        account_id=account.id,
        account_path=book.path(account),
        rows=len(entries),
        revision_before=book.revision,
    )
    proposals = {p.entry.imported_id: p for p in propose(book, account, entries)}
    pool = _PayeePool(book)
    rates = _Rates(book, account.commodity)

    with book.bulk():
        for entry in entries:
            proposal = proposals[entry.imported_id]
            decision = decisions.get(
                entry.imported_id,
                RowDecision(
                    row_key=entry.imported_id,
                    action=RowAction.SKIP if proposal.default_skip else RowAction.POST,
                    payee_id=proposal.suggestion.payee_id,
                    category_id=proposal.suggestion.category_id,
                ),
            )
            if decision.action is RowAction.SKIP:
                _count_skip(report, proposal.status)
                continue
            if book.store.find_imported(entry.imported_id) is not None:
                # Posted between the review screen and now.
                report.skipped_duplicates += 1
                report.note(
                    "duplicate",
                    f"{entry}: already imported since the preview; left alone",
                    source_id=entry.imported_id,
                )
                continue
            _post(
                book,
                account,
                entry,
                decision,
                pool,
                rates,
                report,
                proposal_status=proposal.status,
                uncategorised_account_id=uncategorised_account_id,
            )

    report.payees_created = pool.created
    report.revision_after = book.revision
    return report


def _count_skip(report: StatementReport, status: ProposalStatus) -> None:
    if status is ProposalStatus.DUPLICATE:
        report.skipped_duplicates += 1
    elif status is ProposalStatus.TRANSFER:
        report.skipped_transfers += 1
    else:
        report.skipped_by_user += 1


def _post(
    book: Book,
    account: Account,
    entry: StatementEntry,
    decision: RowDecision,
    pool: _PayeePool,
    rates: _Rates,
    report: StatementReport,
    *,
    proposal_status: ProposalStatus,
    uncategorised_account_id: UUID | None,
) -> None:
    try:
        payee_id = decision.payee_id
        if payee_id is None and decision.new_payee.strip():
            payee_id = pool.resolve(decision.new_payee)

        uncategorised = decision.category_id is None
        category_id = decision.category_id or uncategorised_account_id
        if category_id is None:
            raise ValidationError(
                "no category, and no account was given to collect uncategorised rows"
            )

        currency = account.commodity
        category_commodity = book.account(category_id).commodity
        residual = LegSpec(account_id=category_id)
        if category_commodity.id != currency.id:
            # A cross-currency row: the other leg is in the category's
            # commodity and needs the statement day's rate to be booked.
            residual = LegSpec(
                account_id=category_id,
                price=rates.of(category_commodity, entry.date),
            )
        transaction = book.post(
            entry.date,
            [LegSpec.of(account.id, entry.amount), residual],
            currency=currency.id,
            memo=decision.memo.strip() or entry.memo,
            payee_id=payee_id,
            number=entry.reference[:_NUMBER_MAX],
            imported_id=entry.imported_id,
        )
    except PyMoneyError as exc:
        report.error(
            "transaction",
            f"{entry}: {exc}",
            source_id=entry.imported_id,
        )
        return

    report.posted += 1
    report.posted_ids.append(transaction.id)
    if uncategorised:
        report.uncategorised += 1
    if proposal_status is ProposalStatus.TRANSFER:
        report.note(
            "transfer",
            f"{entry}: posted as a transfer, even though the other side looked "
            f"like it was already recorded",
            source_id=entry.imported_id,
        )

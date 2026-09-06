"""Import a bank statement (CSV) into an account of an existing book.

The KMyMoney importer (:mod:`pymoney.importers.kmymoney`) is a one-time
migration. This is the other half: every week a new statement, dropped into an
account that already has a history, a chart of accounts and a list of payees to
reconcile it against.

Every bank's CSV is different, so the work splits cleanly:

:mod:`~pymoney.importers.statement.mapping`
    :class:`ColumnMapping` -- which column is the date, which is the amount,
    how that amount and that date are written. One per account, remembered.
:mod:`~pymoney.importers.statement.source`
    Bytes in: sniff the delimiter and the header, read the rows, and *guess* a
    mapping so the user confirms rather than builds one from scratch.
:mod:`~pymoney.importers.statement.entry`
    A mapping applied to the rows: :class:`StatementEntry` objects, canonical
    (a ``date``, a signed ``Fraction``, a description) and each already
    carrying the deterministic ``imported_id`` the domain deduplicates on.
:mod:`~pymoney.importers.statement.proposals`
    Entries plus the book: for each row a proposed payee and category (from
    :class:`~pymoney.core.payee.Payee.match_keys`, then the payee's
    ``default_account_id``, then the category it was last filed under), and a
    status -- ``new``, ``duplicate`` (already imported), or ``transfer`` (the
    far side is already in another of your accounts).
:mod:`~pymoney.importers.statement.commit`
    The reviewed, edited proposals, posted in one :meth:`~pymoney.core.book.Book.bulk`
    unit of work. All-or-nothing against the database; one revision per row.

Nothing here reaches a database directly or knows the HTTP layer exists: it
takes a :class:`~pymoney.core.book.Book` and returns plain dataclasses, so the
API, a script and the test suite all drive it the same way.
"""

from __future__ import annotations

from .commit import RowAction, RowDecision, import_statement
from .entry import StatementEntry, entries_from_rows
from .mapping import AmountKind, ColumnMapping, StatementFormatError
from .matching import Suggestion, suggest_for
from .proposals import Proposal, ProposalStatus, propose
from .report import StatementReport
from .source import ReadResult, read_csv, suggest_mapping

__all__ = [
    "AmountKind",
    "ColumnMapping",
    "Proposal",
    "ProposalStatus",
    "ReadResult",
    "RowAction",
    "RowDecision",
    "StatementEntry",
    "StatementFormatError",
    "StatementReport",
    "Suggestion",
    "entries_from_rows",
    "import_statement",
    "propose",
    "read_csv",
    "suggest_for",
    "suggest_mapping",
]

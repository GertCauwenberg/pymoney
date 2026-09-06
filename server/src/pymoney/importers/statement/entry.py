"""A mapping applied to the rows: canonical, deduplicable statement entries.

A :class:`StatementEntry` is the bank's row once the bank has been read out of
it -- a ``date``, a signed amount as an exact :class:`~fractions.Fraction`
(positive means money *into* the account being imported), the text the payee
heuristics match against, and the ``imported_id`` the domain's
``uq_transactions_book_id_imported_id`` constraint deduplicates on.

The ``imported_id`` is the whole reason re-downloading last month's statement
does not double every transaction. Where the bank gives each line its own
reference number, that number *is* the key and overlap is free. Where it does
not, the key is the (date, amount, description) of the row plus its ordinal
among otherwise-identical rows in the same file -- exact for a re-import,
best-effort for a download whose window overlaps the last one.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from uuid import UUID

from .mapping import ColumnMapping

__all__ = ["IMPORT_PREFIX", "StatementEntry", "entries_from_rows", "imported_id_for"]

IMPORT_PREFIX = "csv:"
"""Distinct from the KMyMoney importer's ``kmm:``, so a book that has seen both
can tell a statement row from a migrated one -- and so ``find_imported_prefix``
can answer "has anything been imported from a statement?" with one lookup."""

_DIGEST_LENGTH = 32


@dataclass(frozen=True, slots=True, kw_only=True)
class StatementEntry:
    """One line of a statement, read through a :class:`ColumnMapping`."""

    index: int
    """Zero-based position among the file's data rows. Stable; the review
    screen and the commit refer to a row by its ``imported_id``, but the index
    is what orders them and what disambiguates identical rows."""
    date: date
    amount: Fraction
    """Major units, signed. Positive is money arriving in the account."""
    description: str
    reference: str
    memo: str
    imported_id: str
    raw: dict[str, str]
    """The source row, verbatim, for the review screen and for error messages."""

    @property
    def is_inflow(self) -> bool:
        return self.amount > 0

    def __str__(self) -> str:
        sign = "+" if self.amount >= 0 else ""
        return f"{self.date} {sign}{self.amount} {self.description!r}"


def imported_id_for(
    account_id: UUID,
    *,
    on: date,
    amount: Fraction,
    reference: str,
    description: str,
    occurrence: int,
) -> str:
    """The deterministic dedup key for one statement row.

    Scoped to ``account_id``: the same line imported into two different
    accounts is two different rows, and importing a file into the wrong
    account never raises a false "already imported" against the right one.
    """
    reference = reference.strip()
    if reference:
        discriminator = f"ref:{reference}"
    else:
        discriminator = f"desc:{description.strip()}#{occurrence}"
    material = "\x00".join(
        (
            str(account_id),
            on.isoformat(),
            f"{amount.numerator}/{amount.denominator}",
            discriminator,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    return f"{IMPORT_PREFIX}{digest}"


def entries_from_rows(
    rows: tuple[dict[str, str], ...],
    mapping: ColumnMapping,
    *,
    account_id: UUID,
    columns: tuple[str, ...] | None = None,
) -> list[StatementEntry]:
    """Turn tabulated rows into :class:`StatementEntry` objects.

    :param columns: the file's column names, for an up-front check that the
        mapping does not point at a column the file lacks -- a far better
        error than "row 1: the date column is empty" for every line.
    :raises StatementFormatError: on the first row that does not parse; a
        statement is small and a half-read one is not worth proposing.
    """
    if columns is not None:
        mapping.require_columns(columns)

    seen: Counter[tuple[str, str, str]] = Counter()
    entries: list[StatementEntry] = []
    for index, row in enumerate(rows):
        line = index + 1 + mapping.skip_rows + (1 if mapping.has_header else 0)
        on = mapping.read_date(row, line=line)
        amount = mapping.read_amount(row, line=line)
        reference = row.get(mapping.reference, "").strip() if mapping.reference else ""
        # The two text roles stand in for each other: a file with one
        # descriptive column, mapped to either, fills both -- so the payee
        # matcher always has something to read, and every posted row keeps
        # the bank's own wording even when nothing matched.
        description_text = mapping.read_text(row, mapping.description)
        memo_text = mapping.read_text(row, mapping.memo)
        description = description_text or memo_text
        memo = memo_text or description_text

        fingerprint = (on.isoformat(), f"{amount}", description)
        occurrence = seen[fingerprint]
        seen[fingerprint] += 1
        entries.append(
            StatementEntry(
                index=index,
                date=on,
                amount=amount,
                description=description,
                reference=reference,
                memo=memo,
                imported_id=imported_id_for(
                    account_id,
                    on=on,
                    amount=amount,
                    reference=reference,
                    description=description,
                    occurrence=occurrence,
                ),
                raw=dict(row),
            )
        )
    return entries

"""What a bank's CSV columns mean, and how to read the values out of them.

A :class:`ColumnMapping` is the single thing the user builds once per account
and the server then remembers: it names the date column, the amount column(s),
and the little format decisions every bank makes differently -- a comma for the
decimal point, a trailing minus for an outflow, two columns for debit and
credit instead of one signed one.

Everything here is pure and stdlib-only. The parsers are deliberately lenient
about *presentation* (thousands separators, currency symbols, whitespace) and
strict about *meaning*: a value that does not parse is an error naming the row,
never a silent zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from datetime import date as _Date
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from typing import Any

__all__ = [
    "DATE_FORMATS",
    "AmountKind",
    "ColumnMapping",
    "StatementFormatError",
    "parse_amount",
    "parse_date",
]


class StatementFormatError(ValueError):
    """The file, or a value in it, does not match the mapping.

    A subclass of :class:`ValueError` so the API maps it to 422 the same way it
    maps a bad exact amount: the upload was understood and found wanting.
    """


class AmountKind(StrEnum):
    """How the statement expresses the direction of money.

    ``SIGNED``
        One column. A negative value is money leaving the account. This is by
        far the most common export.
    ``DEBIT_CREDIT``
        Two columns -- one for money out, one for money in -- each holding a
        positive number (or blank). A row fills exactly one.
    """

    SIGNED = "signed"
    DEBIT_CREDIT = "debit_credit"


# Tried in order when ``ColumnMapping.date_format`` is left blank; the first
# that parses *every* row in the file wins, so an unambiguous file needs no
# choice and an ambiguous one (01/02/2025) is the user's to confirm.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%Y%m%d",
)

_GROUPING = str.maketrans({" ": "", "\u00a0": "", "'": "", "_": ""})
# A bare number once the dressing (currency, sign, grouping) is off.
_BARE_NUMBER = re.compile(r"\d+(\.\d+)?|\.\d+")


@dataclass(frozen=True, slots=True, kw_only=True)
class ColumnMapping:
    """Column names and format hints for one bank's CSV.

    Column *names* -- the header text, or ``"1"``, ``"2"`` ... for a file with
    no header. ``description`` may name several columns; they are joined with a
    space, because banks love to split "TESCO STORES 2345" across a payee
    column and a reference column.
    """

    date: str
    date_format: str = ""
    """A ``strptime`` pattern, or ``""`` to detect one from :data:`DATE_FORMATS`."""
    amount_kind: AmountKind = AmountKind.SIGNED
    amount: str = ""
    """The single signed column, for :attr:`AmountKind.SIGNED`."""
    debit: str = ""
    credit: str = ""
    """The money-out and money-in columns, for :attr:`AmountKind.DEBIT_CREDIT`.
    At least one must be named; a bank that only lists withdrawals is fine."""
    decimal: str = "."
    """``"."`` or ``","``. The other of the two, plus spaces, is grouping."""
    invert: bool = False
    """Flip every sign. For the rare export where a withdrawal is positive."""
    description: tuple[str, ...] = ()
    """Columns (joined with a space) for the text the payee heuristics match
    against. Falls back to :attr:`memo` when left empty, so a file with one
    text column need only be mapped once."""
    reference: str = ""
    """The bank's own unique id for the line, if it has one. When present it
    makes the deduplication key stable across overlapping downloads; without
    it, dedup falls back to (date, amount, description, order in the file)."""
    memo: tuple[str, ...] = ()
    """Columns (joined with a space) kept as the transaction's memo. Falls
    back to :attr:`description` when left empty, so every row arrives with the
    bank's own wording even when nothing matched a payee."""
    skip_rows: int = 0
    """Preamble lines above the data (or the header). Some banks print an
    account summary before the transactions."""
    has_header: bool = True
    delimiter: str = ","
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not self.date.strip():
            raise StatementFormatError("a date column is required")
        if self.amount_kind is AmountKind.SIGNED:
            if not self.amount.strip():
                raise StatementFormatError(
                    "a signed-amount mapping needs an amount column"
                )
        elif not self.debit.strip() and not self.credit.strip():
            raise StatementFormatError(
                "a debit/credit mapping needs a debit column, a credit column, or both"
            )
        if self.decimal not in (".", ","):
            raise StatementFormatError(
                f"the decimal separator must be '.' or ',', not {self.decimal!r}"
            )
        if self.skip_rows < 0:
            raise StatementFormatError("skip_rows cannot be negative")
        if len(self.delimiter) != 1:
            raise StatementFormatError("the delimiter must be a single character")
        if self.date_format:
            try:
                datetime.strptime("2001-02-03", "%Y-%m-%d").strftime(self.date_format)
            except ValueError as exc:  # pragma: no cover - defensive
                raise StatementFormatError(
                    f"not a valid date format: {self.date_format!r}"
                ) from exc

    # -- the columns it touches -----------------------------------------------
    @property
    def used_columns(self) -> tuple[str, ...]:
        names = [
            self.date,
            self.amount,
            self.debit,
            self.credit,
            self.reference,
            *self.memo,
            *self.description,
        ]
        seen: list[str] = []
        for name in names:
            cleaned = name.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen)

    def require_columns(self, available: tuple[str, ...]) -> None:
        """:raises StatementFormatError: naming every column the file lacks."""
        have = set(available)
        missing = [name for name in self.used_columns if name not in have]
        if missing:
            raise StatementFormatError(
                "the mapping names column(s) the file does not have: "
                + ", ".join(repr(name) for name in missing)
                + "; the file has: "
                + ", ".join(repr(name) for name in available)
            )

    # -- reading a row --------------------------------------------------------
    def read_date(self, row: dict[str, str], *, line: int) -> _Date:
        raw = row.get(self.date, "").strip()
        if not raw:
            raise StatementFormatError(f"row {line}: the date column is empty")
        return parse_date(raw, self.date_format, line=line)

    def read_amount(self, row: dict[str, str], *, line: int) -> Fraction:
        if self.amount_kind is AmountKind.SIGNED:
            value = parse_amount(row.get(self.amount, ""), self.decimal, line=line)
        else:
            debit = (
                parse_amount(row.get(self.debit, ""), self.decimal, line=line)
                if self.debit
                else Fraction(0)
            )
            credit = (
                parse_amount(row.get(self.credit, ""), self.decimal, line=line)
                if self.credit
                else Fraction(0)
            )
            if debit and credit:
                raise StatementFormatError(
                    f"row {line}: both the debit and the credit column hold a "
                    f"value ({debit} and {credit}); exactly one fills per row"
                )
            # A positive figure in the debit column is money leaving the account.
            value = credit - abs(debit)
        return -value if self.invert else value

    def read_text(self, row: dict[str, str], columns: tuple[str, ...]) -> str:
        parts = [row.get(name, "").strip() for name in columns]
        return " ".join(part for part in parts if part)

    def describe(self) -> str:
        which = (
            f"amount={self.amount!r}"
            if self.amount_kind is AmountKind.SIGNED
            else f"debit={self.debit!r} credit={self.credit!r}"
        )
        return f"date={self.date!r} {which} decimal={self.decimal!r}"

    # -- persistence (one row of the import_mappings table) ------------------
    def to_dict(self) -> dict[str, Any]:
        """A JSON-friendly dict: every value a string, int, bool or list."""
        data: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, tuple):
                value = list(value)
            elif isinstance(value, AmountKind):
                value = value.value
            data[field.name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnMapping:
        """Rebuild from :meth:`to_dict`, ignoring any key this version of the
        class has since dropped, and coercing a value stored under an older
        shape (``memo`` used to be a single string)."""
        known = {f.name for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for name, value in data.items():
            if name not in known:
                continue
            if name in ("description", "memo"):
                if isinstance(value, str):
                    value = [value] if value else []
                kwargs[name] = tuple(value or ())
            elif name == "amount_kind":
                kwargs[name] = AmountKind(value)
            else:
                kwargs[name] = value
        return cls(**kwargs)

    def replace(self, **changes: Any) -> ColumnMapping:
        return replace(self, **changes)


def parse_date(text: str, fmt: str, *, line: int | None = None) -> _Date:
    """Parse one date cell. With ``fmt`` blank, try :data:`DATE_FORMATS`."""
    text = text.strip()
    formats = (fmt,) if fmt else DATE_FORMATS
    for candidate in formats:
        try:
            return datetime.strptime(text, candidate).date()
        except ValueError:
            continue
    where = "" if line is None else f"row {line}: "
    shown = repr(fmt) if fmt else "any recognised format"
    raise StatementFormatError(f"{where}{text!r} is not a date in {shown}")


def parse_amount(text: str, decimal: str, *, line: int | None = None) -> Fraction:
    """Parse one money cell into an exact :class:`~fractions.Fraction`.

    Forgiving about how it is dressed -- a leading ``$`` or ``EUR``, thousands
    separators, a space, ``CR``/``DR`` suffixes, parentheses or a trailing
    minus for a negative -- and exact about the number underneath it. An empty
    cell is zero; anything that is not a single well-formed number after the
    dressing is removed is an error, including a comma where the mapping
    promised a full stop (the commonest way an import goes quietly wrong).
    """
    where = "" if line is None else f"row {line}: "
    body = text.strip()
    if not body:
        return Fraction(0)

    def fail() -> StatementFormatError:
        return StatementFormatError(f"{where}{text!r} is not an amount")

    negative = False
    if body[-2:].upper() == "CR":
        body = body[:-2].strip()
    elif body[-2:].upper() == "DR":
        body, negative = body[:-2].strip(), True
    if body.startswith("(") and body.endswith(")"):
        body, negative = body[1:-1].strip(), True

    # Currency symbols and codes decorate the ends, not the middle; one sign
    # may sit at either end. What is left has to be the number itself -- a
    # stray character *inside* it (a slash, a second sign) is an error, not
    # something to quietly delete.
    core = re.sub(r"^[^0-9(+-]+", "", body)
    core = re.sub(r"[^0-9)+-]+$", "", core).strip()
    if core.endswith(("-", "+")):
        negative = negative or core.endswith("-")
        core = core[:-1].strip()
    if core.startswith(("+", "-")):
        negative = negative or core.startswith("-")
        core = core[1:].strip()
    core = core.translate(_GROUPING)

    grouping = "," if decimal == "." else "."
    if grouping in core and core.rfind(grouping) > core.rfind(decimal):
        # A grouping mark cannot sit to the right of the decimal point; this
        # almost always means a European file read with an Anglo mapping, or
        # the reverse, and the whole column would be wrong by 100x.
        raise StatementFormatError(
            f"{where}{text!r} is not an amount with {decimal!r} for the decimal "
            f"separator -- is the file's separator {grouping!r}?"
        )
    core = core.replace(grouping, "")
    if decimal == ",":
        core = core.replace(",", ".")
    if not _BARE_NUMBER.fullmatch(core):
        raise fail()
    magnitude = Fraction(core)
    return -magnitude if negative else magnitude

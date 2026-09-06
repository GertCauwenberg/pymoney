"""Bytes in: a delimiter, a header, the rows -- and a mapping to start from.

The user should *confirm* a mapping, not build one from nothing, so this module
sniffs as much as it safely can: the text encoding, the field delimiter,
whether the first row is a header, and which column is most likely the date,
the amount and the description. Every guess is visible and every guess is
overridable; none of it is trusted once the real parse runs.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime

from .mapping import DATE_FORMATS, AmountKind, ColumnMapping, parse_amount, parse_date

__all__ = ["ReadResult", "read_csv", "suggest_mapping"]

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_DELIMITERS = (",", ";", "\t", "|")
_MAX_SNIFF_BYTES = 64 * 1024

# ``exact`` names are matched only against the whole header cell (``"in"`` is a
# column on one bank and a fragment of "instruction" everywhere else);
# ``contains`` names may match a substring.
_Hints = tuple[tuple[str, ...], tuple[str, ...]]
_DATE_HINTS: _Hints = (
    ("date", "datum", "dato", "fecha", "boekdatum", "transactiedatum"),
    ("date", "datum", "posted", "transaction date", "value date", "buchung"),
)
_AMOUNT_HINTS: _Hints = (
    ("amount", "bedrag", "betrag", "montant", "importe", "mutatie", "value"),
    ("amount", "bedrag", "betrag", "transaction amount", "mutatiebedrag"),
)
_DEBIT_HINTS: _Hints = (
    (
        "debit",
        "debet",
        "out",
        "uit",
        "soll",
        "af",
        "withdrawal",
        "paid out",
        "money out",
        "bedrag af",
    ),
    ("debit", "withdrawal", "paid out", "money out", "outgoing", "af bij"),
)
_CREDIT_HINTS: _Hints = (
    ("credit", "deposit", "in", "bij", "haben", "paid in", "money in", "bedrag bij"),
    ("credit", "deposit", "paid in", "money in", "incoming"),
)
_DESC_HINTS: _Hints = (
    (
        "description",
        "omschrijving",
        "payee",
        "merchant",
        "name",
        "naam",
        "memo",
        "details",
        "narrative",
        "beschreibung",
        "concepto",
        "counterparty",
    ),
    (
        "description",
        "omschrijving",
        "payee",
        "merchant",
        "narrative",
        "details",
        "naam tegenrekening",
        "name",
        "beschreibung",
        "concept",
    ),
)
_REFERENCE_HINTS: _Hints = (
    (
        "reference",
        "ref",
        "transaction id",
        "bank reference",
        "id",
        "volgnr",
        "cheque",
        "check no",
        "kenmerk",
    ),
    ("reference", "transaction id", "bank reference", "cheque"),
)
_MEMO_HINTS: _Hints = (
    ("memo", "notes", "note", "mededelingen", "comment", "opmerkingen"),
    ("memo", "notes", "mededeling", "remark"),
)
_IGNORE_FOR_NUMBERS = re.compile(r"[^0-9]")


@dataclass(frozen=True, slots=True)
class ReadResult:
    """A decoded, tabular view of the upload -- before any mapping is applied."""

    columns: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    encoding: str
    delimiter: str
    has_header: bool
    skip_rows: int
    truncated_sample: tuple[dict[str, str], ...] = field(default_factory=tuple)
    """The first handful of rows, for the column-mapping screen."""

    @property
    def row_count(self) -> int:
        return len(self.rows)


def read_csv(
    data: bytes,
    *,
    encoding: str | None = None,
    delimiter: str | None = None,
    has_header: bool | None = None,
    skip_rows: int | None = None,
    sample: int = 12,
) -> ReadResult:
    """Decode and tabulate ``data``. Any argument left ``None`` is sniffed.

    Columns are the header text when there is a header, and ``"1"``, ``"2"`` ...
    otherwise. Short rows are padded and long rows truncated to the column
    count, so a stray trailing delimiter does not derail the whole file.
    """
    text, used_encoding = _decode(data, encoding)
    lines = text.splitlines()
    start = skip_rows if skip_rows is not None else _leading_noise(lines)
    body = lines[start:]
    if not any(line.strip() for line in body):
        from .mapping import StatementFormatError

        raise StatementFormatError("the file has no data rows")

    used_delimiter = delimiter or _sniff_delimiter(body)
    records = list(csv.reader(body, delimiter=used_delimiter))
    records = [r for r in records if any(cell.strip() for cell in r)]

    header_present = has_header if has_header is not None else _looks_like_header(records)
    width = max(len(r) for r in records)
    if header_present:
        columns = _name_columns(records[0], width)
        data_rows = records[1:]
    else:
        columns = tuple(str(i + 1) for i in range(width))
        data_rows = records

    rows = tuple(_as_dict(record, columns) for record in data_rows)
    return ReadResult(
        columns=columns,
        rows=rows,
        encoding=used_encoding,
        delimiter=used_delimiter,
        has_header=header_present,
        skip_rows=start,
        truncated_sample=rows[:sample],
    )


def suggest_mapping(read: ReadResult) -> ColumnMapping:
    """A best-effort :class:`ColumnMapping` for ``read``, for the user to confirm.

    Header names are matched against a multilingual list of hints; where there
    is no header, or no hint fits, the columns are scored by their *content*:
    the one that parses as a date in every row, the one that looks most like
    money. The decimal separator and date format are inferred from the values.
    """
    columns = read.columns
    rows = read.rows

    def by_hint(hints: _Hints) -> str:
        exact, contains = hints
        for column in columns:
            if column.strip().casefold() in exact:
                return column
        for column in columns:
            folded = column.strip().casefold()
            if any(fragment in folded for fragment in contains):
                return column
        return ""

    date_col = by_hint(_DATE_HINTS) or _date_column(columns, rows)
    decimal = _decimal_separator(columns, rows)
    amount_col = by_hint(_AMOUNT_HINTS)
    debit_col = by_hint(_DEBIT_HINTS)
    credit_col = by_hint(_CREDIT_HINTS)
    if not amount_col and not debit_col and not credit_col:
        amount_col = _amount_column(columns, rows, decimal, exclude={date_col})

    kind = AmountKind.SIGNED
    if (debit_col or credit_col) and not amount_col:
        kind = AmountKind.DEBIT_CREDIT
    elif amount_col:
        debit_col = credit_col = ""

    reference_col = by_hint(_REFERENCE_HINTS)
    memo_col = by_hint(_MEMO_HINTS)
    numeric = {date_col, amount_col, debit_col, credit_col, ""}

    named = tuple(
        c
        for c in columns
        if c not in numeric
        and c != reference_col
        and c != memo_col
        and c.strip().casefold() in set(_DESC_HINTS[0])
    )
    description = named or _text_columns(
        columns, rows, exclude=numeric | {reference_col, memo_col}
    )
    memo: tuple[str, ...] = (memo_col,) if memo_col else ()
    if not description and memo:
        # A file whose only free text is headed "Memo" (or "Notes"): the payee
        # matcher needs something to read, and the entry builder folds the two
        # roles together anyway, so one column serves both.
        description, memo = memo, ()

    return ColumnMapping(
        date=date_col or (columns[0] if columns else "1"),
        date_format=_date_format(date_col, rows) if date_col else "",
        amount_kind=kind,
        amount=amount_col if kind is AmountKind.SIGNED else "",
        debit=debit_col if kind is AmountKind.DEBIT_CREDIT else "",
        credit=credit_col if kind is AmountKind.DEBIT_CREDIT else "",
        decimal=decimal,
        description=description,
        reference=reference_col,
        memo=memo,
        skip_rows=read.skip_rows,
        has_header=read.has_header,
        delimiter=read.delimiter,
        encoding=read.encoding,
    )


# -- decoding & sniffing ------------------------------------------------------
def _decode(data: bytes, encoding: str | None) -> tuple[str, str]:
    if encoding:
        return data.decode(encoding), encoding
    for candidate in _ENCODINGS:
        try:
            return data.decode(candidate), candidate
        except UnicodeDecodeError:
            continue
    # latin-1 maps every byte, so this is only reached for an empty tuple.
    return data.decode("latin-1", errors="replace"), "latin-1"  # pragma: no cover


def _leading_noise(lines: list[str]) -> int:
    """How many preamble lines sit above the table.

    A bank that prints a summary block first leaves blank lines or one-cell
    lines before the real header; the table starts at the first line whose
    delimiter count matches the lines that follow it.
    """
    candidates = [i for i, line in enumerate(lines) if line.strip()]
    if len(candidates) < 3:
        return candidates[0] if candidates else 0
    for index in candidates:
        counts = {max(lines[index].count(d), 0) for d in _DELIMITERS if d in lines[index]}
        following = candidates[candidates.index(index) + 1 : candidates.index(index) + 4]
        if not counts or not following:
            continue
        best = max(counts)
        if best >= 1 and all(
            any(lines[j].count(d) == best for d in _DELIMITERS) for j in following
        ):
            return index
    return candidates[0]


def _sniff_delimiter(lines: list[str]) -> str:
    """The candidate most rows agree on the field count for.

    Preferred over :class:`csv.Sniffer`, which a European file -- ``;`` between
    the fields, ``,`` inside every amount -- routinely fools into picking the
    comma. Here the tie-break is the wider table: ``;`` giving five columns on
    every line beats ``,`` giving two.
    """
    from collections import Counter

    sample = [line for line in lines[:40] if line.strip()]
    best, best_score = ",", (0, 0)
    for candidate in _DELIMITERS:
        widths = Counter(len(row) for row in csv.reader(sample, delimiter=candidate))
        width, agree = max(widths.items(), key=lambda wc: (wc[1], wc[0]))
        if width < 2:
            continue
        score = (agree, width)
        if score > best_score:
            best, best_score = candidate, score
    return best


def _looks_like_header(records: list[list[str]]) -> bool:
    if len(records) < 2:
        return True
    first, rest = records[0], records[1:6]

    def numeric_share(record: list[str]) -> float:
        cells = [c.strip() for c in record if c.strip()]
        if not cells:
            return 0.0
        numeric = sum(1 for c in cells if _IGNORE_FOR_NUMBERS.sub("", c))
        return numeric / len(cells)

    first_numeric = numeric_share(first)
    body_numeric = sum(numeric_share(r) for r in rest) / len(rest)
    return first_numeric + 0.25 < body_numeric or first_numeric == 0.0


def _name_columns(header: list[str], width: int) -> tuple[str, ...]:
    names: list[str] = []
    for index in range(width):
        raw = header[index].strip() if index < len(header) else ""
        name = raw or f"column {index + 1}"
        unique, suffix = name, 2
        while unique in names:
            unique, suffix = f"{name} ({suffix})", suffix + 1
        names.append(unique)
    return tuple(names)


def _as_dict(record: list[str], columns: tuple[str, ...]) -> dict[str, str]:
    padded = list(record) + [""] * (len(columns) - len(record))
    return {name: padded[i].strip() for i, name in enumerate(columns)}


# -- content-based column guessing -------------------------------------------
def _column_values(rows: tuple[dict[str, str], ...], column: str) -> list[str]:
    return [v for v in (row.get(column, "").strip() for row in rows) if v][:200]


def _date_column(columns: tuple[str, ...], rows: tuple[dict[str, str], ...]) -> str:
    for column in columns:
        values = _column_values(rows, column)
        if values and all(_is_date(v) for v in values):
            return column
    return ""


def _is_date(value: str) -> bool:
    from .mapping import StatementFormatError

    try:
        parse_date(value, "")
    except StatementFormatError:
        return False
    return True


def _date_format(column: str, rows: tuple[dict[str, str], ...]) -> str:
    values = _column_values(rows, column)
    for fmt in DATE_FORMATS:
        if values and all(_parses(v, fmt) for v in values):
            return "" if fmt == DATE_FORMATS[0] else fmt
    return ""


def _parses(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
    except ValueError:
        return False
    return True


def _decimal_separator(columns: tuple[str, ...], rows: tuple[dict[str, str], ...]) -> str:
    comma = dot = 0
    for column in columns:
        for value in _column_values(rows, column):
            tail = re.sub(r"[^0-9.,]", "", value)
            if "," in tail and "." in tail:
                comma += tail.rfind(",") > tail.rfind(".")
                dot += tail.rfind(".") > tail.rfind(",")
            elif re.search(r",\d{1,2}$", tail):
                comma += 1
            elif re.search(r"\.\d{1,2}$", tail):
                dot += 1
    return "," if comma > dot else "."


def _amount_column(
    columns: tuple[str, ...],
    rows: tuple[dict[str, str], ...],
    decimal: str,
    *,
    exclude: set[str],
) -> str:
    best, best_score = "", 0.0
    for column in columns:
        if column in exclude or not column:
            continue
        values = _column_values(rows, column)
        if not values:
            continue
        parsed = [_safe_amount(value, decimal) for value in values]
        if any(amount is None for amount in parsed):
            continue
        # Every value parses -- but so does a running-balance column. The
        # transaction amount is the one that is sometimes negative.
        has_negative = any(amount < 0 for amount in parsed if amount is not None)
        score = 1.0 + (0.5 if has_negative else 0.0)
        if score > best_score:
            best, best_score = column, score
    return best


def _safe_amount(value: str, decimal: str):
    from .mapping import StatementFormatError

    try:
        return parse_amount(value, decimal)
    except StatementFormatError:
        return None


def _text_columns(
    columns: tuple[str, ...],
    rows: tuple[dict[str, str], ...],
    *,
    exclude: set[str],
) -> tuple[str, ...]:
    """The column(s) most likely to carry the merchant / description text.

    Scored by how many *letters* a value holds on average, so a column of
    ``SEPA /9912/ ALBERT HEIJN`` beats a column of cheque numbers even though
    both "have a letter". A second column is taken only when it is almost as
    wordy as the first (banks that split the payee across two fields); and a
    file with nothing obviously texty still gets its widest remaining column,
    because an approximate memo beats a blank row to post against.
    """
    scored: list[tuple[float, str]] = []
    for column in columns:
        if column in exclude or not column:
            continue
        values = _column_values(rows, column)
        if not values:
            continue
        letters = sum(sum(ch.isalpha() for ch in v) for v in values) / len(values)
        scored.append((letters, column))
    if not scored:
        return ()
    scored.sort(key=lambda pair: (-pair[0], columns.index(pair[1])))
    top = scored[0]
    chosen = [top[1]]
    for letters, column in scored[1:]:
        if letters >= max(3.0, top[0] * 0.6):
            chosen.append(column)
    return tuple(chosen[:2])

"""Reading a ``.kmy`` file.

A KMyMoney file is XML, usually gzipped, optionally GPG-encrypted. Everything
except the transactions is small -- a few hundred accounts, payees, currencies
and prices -- while the transactions are unbounded. So :class:`KmyFile` reads the
small parts eagerly and streams the transactions on demand, which keeps memory
flat whether the book has a thousand postings or a million.

The awkward part is that KMyMoney writes ``<TRANSACTIONS>`` *before*
``<SECURITIES>``, ``<CURRENCIES>`` and ``<PRICES>``, so the commodities a
transaction refers to are only known after the transactions have gone past. The
file is therefore read twice: once for the small sections, once for the
transactions. Decompressing twice is cheaper than holding the ledger in memory,
and a one-off import can afford it either way.

The XML is parsed with ``defusedxml``, not the standard library directly. A
``.kmy`` used to arrive only from the user's own disk; since the web app's
import endpoint, anyone with an account can upload one, and an XML file can be
hostile: entity expansion ("billion laughs"), external entities that read
local files or call out to the network. ``defusedxml`` refuses all of that
with a :class:`KmyFormatError`-wrapped exception rather than expanding it. The
same goes for the other half of the file format: a gzip stream is read through
a cap on the *decompressed* size, because a 50 MB upload limit says nothing
about a 50 MB gzip of zeros that inflates to fifty gigabytes.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import IO, Any, ClassVar
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import iterparse

from .model import (
    KmyAccount,
    KmyCommodity,
    KmyFileInfo,
    KmyInstitution,
    KmyPayee,
    KmyPrice,
    KmySchedule,
    KmySplit,
    KmyTransaction,
)

__all__ = [
    "DEFAULT_MAX_XML_BYTES",
    "KmyFile",
    "KmyFormatError",
    "KmyTooLargeError",
    "detect_format",
    "open_kmy",
]

DEFAULT_MAX_XML_BYTES = 2 * 1024**3
"""How much XML a file may inflate to before we stop reading: 2 GiB, which is
more than a lifetime of KMyMoney and less than a gzip bomb."""

GZIP_MAGIC = b"\x1f\x8b"
PGP_ARMOUR = b"-----BEGIN PGP MESSAGE-----"
PGP_BINARY = (b"\x85", b"\x8c", b"\xc1")


class KmyFormatError(Exception):
    """The file is not a KMyMoney file we can read."""


class KmyTooLargeError(KmyFormatError):
    """The file inflates past the size we are prepared to read."""


class _Capped(io.RawIOBase):
    """A read-only stream that refuses to hand out more than ``limit`` bytes."""

    def __init__(self, inner: IO[bytes], limit: int, what: str) -> None:
        self._inner = inner
        self._limit = limit
        self._what = what
        self._seen = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        chunk = self._inner.read(len(buffer))
        self._seen += len(chunk)
        if self._seen > self._limit:
            raise KmyTooLargeError(
                f"{self._what} inflates past {self._limit // 1024**2} MB; refusing to read on"
            )
        buffer[: len(chunk)] = chunk
        return len(chunk)

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            super().close()


def detect_format(path: Path) -> str:
    """Return ``"gzip"``, ``"xml"`` or ``"gpg"``.

    :raises KmyFormatError: if the file is empty or unrecognisable.
    """
    with path.open("rb") as handle:
        head = handle.read(64)
    if not head:
        raise KmyFormatError(f"{path} is empty")
    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if head.startswith(PGP_ARMOUR) or head[:1] in PGP_BINARY:
        return "gpg"
    stripped = head.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<!DOCTYPE"):
        return "xml"
    raise KmyFormatError(
        f"{path} is neither gzipped nor XML; KMyMoney files start with one or the other"
    )


@contextmanager
def open_kmy(
    path: Path, *, max_bytes: int = DEFAULT_MAX_XML_BYTES
) -> Iterator[IO[bytes]]:
    """Open a ``.kmy`` file as a binary stream of XML.

    :param max_bytes: the most XML the stream may yield, compressed or not;
        past it, reading raises :class:`KmyTooLargeError`.
    """
    kind = detect_format(path)
    if kind == "gpg":
        raise KmyFormatError(
            f"{path} is GPG-encrypted. Decrypt it first:\n"
            f"    gpg --decrypt {path} > plain.kmy\n"
            f"We deliberately do not shell out to gpg: your passphrase is not "
            f"ours to handle."
        )
    # Closed in the finally below; this *is* the context manager.
    raw: Any = gzip.open(path, "rb") if kind == "gzip" else path.open("rb")  # noqa: SIM115
    handle: Any = io.BufferedReader(_Capped(raw, max_bytes, str(path)))
    try:
        yield handle
    finally:
        handle.close()


# -- attribute helpers -------------------------------------------------------


def _text(element: Element, name: str, default: str = "") -> str:
    return element.get(name, default) or default


def _int(element: Element, name: str, default: int = 0) -> int:
    raw = element.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _optional_int(element: Element, name: str) -> int | None:
    raw = element.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _date(element: Element, name: str) -> date | None:
    """Parse KMyMoney's date attribute. Usually ``YYYY-MM-DD``; some versions
    emit a full ISO timestamp (``2026-08-22T08:23:17+02:00``) and we take the
    date portion."""
    raw = element.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def parse_rational(raw: str | None, default: Fraction | None = None) -> Fraction | None:
    """Parse KMyMoney's ``"num/den"`` (or a bare integer).

    Returns ``default`` for missing or unparseable input rather than raising, so
    one bad attribute in a 100k-transaction file does not abort the import; the
    importer reports what it had to fall back on.
    """
    if raw is None or raw.strip() == "":
        return default
    try:
        return Fraction(raw.strip())
    except (ValueError, ZeroDivisionError):
        return default


def _key_values(element: Element) -> dict[str, str]:
    pairs = element.find("KEYVALUEPAIRS")
    if pairs is None:
        return {}
    return {
        _text(pair, "key"): _text(pair, "value")
        for pair in pairs.findall("PAIR")
        if pair.get("key")
    }


# -- the file ----------------------------------------------------------------


@dataclass
class KmyFile:
    """A parsed KMyMoney file: metadata eager, transactions streamed."""

    path: Path
    info: KmyFileInfo = field(default_factory=KmyFileInfo)
    currencies: dict[str, KmyCommodity] = field(default_factory=dict)
    securities: dict[str, KmyCommodity] = field(default_factory=dict)
    accounts: dict[str, KmyAccount] = field(default_factory=dict)
    payees: dict[str, KmyPayee] = field(default_factory=dict)
    institutions: dict[str, KmyInstitution] = field(default_factory=dict)
    prices: list[KmyPrice] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    schedules: list[KmySchedule] = field(default_factory=list)
    key_values: dict[str, str] = field(default_factory=dict)
    max_bytes: int = DEFAULT_MAX_XML_BYTES
    """The most XML the file may inflate to (see :func:`open_kmy`)."""
    """The file's own settings, notably ``kmm-baseCurrency``."""
    declared_counts: dict[str, int] = field(default_factory=dict)
    """The ``count`` attribute of each section, as the file claims it."""

    @property
    def base_currency_id(self) -> str:
        return self.key_values.get("kmm-baseCurrency", "")

    # -- loading -----------------------------------------------------------
    @classmethod
    def read(cls, path: Path | str, *, max_bytes: int = DEFAULT_MAX_XML_BYTES) -> KmyFile:
        """Read everything except the transactions."""
        path = Path(path)
        parsed = cls(path=path, max_bytes=max_bytes)
        try:
            parsed._read(path)
        except ParseError as exc:
            # A truncated or hand-edited file should say so, not surface an XML
            # traceback at someone migrating ten years of records.
            raise KmyFormatError(f"{path} is not valid XML: {exc}") from exc
        except DefusedXmlException as exc:
            raise KmyFormatError(
                f"{path} uses XML features a KMyMoney file never needs "
                f"({type(exc).__name__}); refusing to read it"
            ) from exc
        return parsed

    def _read(self, path: Path) -> None:
        parsed = self
        with open_kmy(path, max_bytes=self.max_bytes) as stream:
            root_seen = False
            depth = 0
            for event, element in iterparse(stream, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if not root_seen:
                        if element.tag != "KMYMONEY-FILE":
                            raise KmyFormatError(
                                f"{path}: expected a KMYMONEY-FILE root element, "
                                f"found <{element.tag}>"
                            )
                        root_seen = True
                    continue
                depth -= 1

                if element.tag == "KEYVALUEPAIRS":
                    # Accounts and securities have these too, so the depth is
                    # what distinguishes the file's own settings from a row's.
                    if depth == 1:
                        parsed.key_values.update(
                            {
                                pair.get("key", ""): pair.get("value", "")
                                for pair in element.findall("PAIR")
                                if pair.get("key")
                            }
                        )
                        element.clear()
                    continue

                handler = parsed._SECTIONS.get(element.tag)
                if handler is not None:
                    handler(parsed, element)
                    element.clear()
                elif element.tag == "TRANSACTIONS":
                    parsed.declared_counts["transactions"] = _int(element, "count", -1)
                    element.clear()
                elif element.tag == "SCHEDULES":
                    parsed.declared_counts["schedules"] = _int(element, "count", -1)
                    _read_schedules(parsed, element)
                    element.clear()
                elif element.tag in _COUNTED_SECTIONS:
                    parsed.declared_counts[element.tag.lower()] = _int(
                        element, "count", -1
                    )
                    element.clear()
            if not root_seen:
                raise KmyFormatError(f"{path}: no XML elements found")

    def iter_transactions(self) -> Iterator[KmyTransaction]:
        """Stream the real transactions, one at a time, in file order.

        Only those directly inside ``<TRANSACTIONS>``. A ``<SCHEDULED_TX>``
        contains a ``<TRANSACTION>`` too -- the template for a future payment
        that has not happened -- and importing those as real postings would
        invent money out of a standing order.
        """
        try:
            yield from self._iter_transactions()
        except ParseError as exc:
            raise KmyFormatError(f"{self.path} is not valid XML: {exc}") from exc
        except DefusedXmlException as exc:
            raise KmyFormatError(
                f"{self.path} uses XML features a KMyMoney file never needs "
                f"({type(exc).__name__}); refusing to read it"
            ) from exc

    def _iter_transactions(self) -> Iterator[KmyTransaction]:
        with open_kmy(self.path, max_bytes=self.max_bytes) as stream:
            stack: list[str] = []
            for event, element in iterparse(stream, events=("start", "end")):
                if event == "start":
                    stack.append(element.tag)
                    continue
                tag = stack.pop() if stack else element.tag
                if tag != "TRANSACTION":
                    continue
                if not stack or stack[-1] != "TRANSACTIONS":
                    element.clear()
                    continue
                yield _transaction(element)
                # The whole point of streaming: let it go.
                element.clear()

    # -- lookups -----------------------------------------------------------
    def commodity(self, reference: str) -> KmyCommodity | None:
        """Resolve a commodity reference from an account or a price pair.

        Files in the wild reference commodities by id (``USD``, ``E000001``) and
        sometimes by a security's trading symbol, so all three are tried.
        """
        if not reference:
            return None
        for table in (self.currencies, self.securities):
            found = table.get(reference)
            if found is not None:
                return found
        for security in self.securities.values():
            if security.symbol and security.symbol == reference:
                return security
        return None

    @property
    def commodities(self) -> dict[str, KmyCommodity]:
        return {**self.currencies, **self.securities}

    # -- section handlers --------------------------------------------------
    def _read_fileinfo(self, element: Element) -> None:
        def sub_date(tag: str) -> date | None:
            child = element.find(tag)
            return None if child is None else _date(child, "date")

        version = element.find("VERSION")
        fix = element.find("FIXVERSION")
        self.info = KmyFileInfo(
            creation_date=sub_date("CREATION_DATE"),
            last_modified=sub_date("LAST_MODIFIED_DATE"),
            version="" if version is None else _text(version, "id"),
            fix_version="" if fix is None else _text(fix, "id"),
        )

    def _read_currencies(self, element: Element) -> None:
        self.declared_counts["currencies"] = _int(element, "count", -1)
        for child in element.findall("CURRENCY"):
            identifier = _text(child, "id")
            if not identifier:
                continue
            self.currencies[identifier] = KmyCommodity(
                id=identifier,
                name=_text(child, "name"),
                symbol=_text(child, "symbol"),
                type=_int(child, "type"),
                is_currency=True,
                smallest_account_fraction=_int(child, "saf", 100) or 100,
                smallest_cash_fraction=_optional_int(child, "scf"),
                price_precision=_optional_int(child, "ppp"),
            )

    def _read_securities(self, element: Element) -> None:
        self.declared_counts["securities"] = _int(element, "count", -1)
        for child in element.findall("SECURITY"):
            identifier = _text(child, "id")
            if not identifier:
                continue
            pairs = _key_values(child)
            self.securities[identifier] = KmyCommodity(
                id=identifier,
                name=_text(child, "name"),
                symbol=_text(child, "symbol"),
                type=_int(child, "type"),
                is_currency=False,
                smallest_account_fraction=_int(child, "saf", 10000) or 10000,
                price_precision=_optional_int(child, "pp"),
                trading_currency=_text(child, "trading-currency"),
                trading_market=_text(child, "trading-market"),
                isin=pairs.get("kmm-security-id", "") or pairs.get("isin", ""),
            )

    def _read_accounts(self, element: Element) -> None:
        self.declared_counts["accounts"] = _int(element, "count", -1)
        for child in element.findall("ACCOUNT"):
            identifier = _text(child, "id")
            if not identifier:
                continue
            self.accounts[identifier] = KmyAccount(
                id=identifier,
                name=_text(child, "name"),
                parent_id=_text(child, "parentaccount"),
                type=_int(child, "type"),
                currency_id=_text(child, "currency"),
                description=_text(child, "description"),
                number=_text(child, "number"),
                institution_id=_text(child, "institution"),
                opened=_date(child, "opened"),
                last_reconciled=_date(child, "lastreconciled"),
                key_values=_key_values(child),
            )

    def _read_payees(self, element: Element) -> None:
        self.declared_counts["payees"] = _int(element, "count", -1)
        for child in element.findall("PAYEE"):
            identifier = _text(child, "id")
            if not identifier:
                continue
            address = child.find("ADDRESS")
            parts = (
                []
                if address is None
                else [
                    _text(address, key)
                    for key in ("street", "postcode", "city", "state", "country")
                ]
            )
            self.payees[identifier] = KmyPayee(
                id=identifier,
                name=_text(child, "name"),
                email=_text(child, "email"),
                notes=_text(child, "notes"),
                address=", ".join(p for p in parts if p),
            )

    def _read_institutions(self, element: Element) -> None:
        self.declared_counts["institutions"] = _int(element, "count", -1)
        for child in element.findall("INSTITUTION"):
            identifier = _text(child, "id")
            if identifier:
                self.institutions[identifier] = KmyInstitution(
                    id=identifier, name=_text(child, "name")
                )

    def _read_prices(self, element: Element) -> None:
        self.declared_counts["prices"] = _int(element, "count", -1)
        for pair in element.findall("PRICEPAIR"):
            from_id = _text(pair, "from")
            to_id = _text(pair, "to")
            for price in pair.findall("PRICE"):
                when = _date(price, "date")
                rate = parse_rational(price.get("price"))
                if when is None or rate is None or rate <= 0:
                    continue
                self.prices.append(
                    KmyPrice(
                        from_id=from_id,
                        to_id=to_id,
                        date=when,
                        rate=rate,
                        source=_text(price, "source"),
                    )
                )

    def _read_tags(self, element: Element) -> None:
        self.declared_counts["tags"] = _int(element, "count", -1)
        for child in element.findall("TAG"):
            identifier = _text(child, "id")
            if identifier:
                self.tags[identifier] = _text(child, "name")

    _SECTIONS: ClassVar[dict[str, Any]] = {
        "FILEINFO": _read_fileinfo,
        "CURRENCIES": _read_currencies,
        "SECURITIES": _read_securities,
        "ACCOUNTS": _read_accounts,
        "PAYEES": _read_payees,
        "INSTITUTIONS": _read_institutions,
        "PRICES": _read_prices,
        "TAGS": _read_tags,
    }


# Sections we do not read, but whose size the user should be told about.
_COUNTED_SECTIONS = frozenset(
    {"SCHEDULES", "BUDGETS", "REPORTS", "COSTCENTERS", "ONLINEJOBS"}
)


def _read_schedules(parsed: KmyFile, element: Element) -> None:
    """Parse <SCHEDULE> children: name, the recurrence code, and the embedded
    <TRANSACTION> that is the template (its postdate is the next due date)."""
    for sched in element.findall("SCHEDULED_TX"):
        txn_el = sched.find("TRANSACTION")
        parsed.schedules.append(
            KmySchedule(
                id=sched.get("id", ""),
                name=sched.get("name", "").strip(),
                occurrence=_int(sched, "occurence")  # KMyMoney's spelling
                or _int(sched, "occurrence"),
                occurrence_multiplier=_int(sched, "occurenceMultiplier", 1)
                or _int(sched, "occurrenceMultiplier", 1),
                start_date=_date(sched, "startDate"),
                end_date=_date(sched, "endDate"),
                last_payment=_date(sched, "lastPayment"),
                auto_enter=sched.get("autoEnter", "0") == "1",
                fixed=sched.get("fixed", "1") == "1",
                transaction=_transaction(txn_el) if txn_el is not None else None,
            )
        )


def _transaction(element: Element) -> KmyTransaction:
    splits_element = element.find("SPLITS")
    splits = []
    if splits_element is not None:
        for index, split in enumerate(splits_element.findall("SPLIT")):
            shares = parse_rational(split.get("shares"), Fraction(0))
            value = parse_rational(split.get("value"), Fraction(0))
            price = parse_rational(split.get("price"))
            assert shares is not None and value is not None
            splits.append(
                KmySplit(
                    id=_text(split, "id") or f"S{index:04d}",
                    account_id=_text(split, "account"),
                    payee_id=_text(split, "payee"),
                    shares=shares,
                    value=value,
                    price=price,
                    memo=_text(split, "memo"),
                    action=_text(split, "action"),
                    number=_text(split, "number"),
                    bank_id=_text(split, "bankid"),
                    reconcile_flag=_int(split, "reconcileflag"),
                    reconcile_date=_date(split, "reconciledate"),
                    tag_ids=tuple(
                        _text(tag, "id") for tag in split.findall("TAG") if tag.get("id")
                    ),
                )
            )
    return KmyTransaction(
        id=_text(element, "id"),
        post_date=_date(element, "postdate"),
        entry_date=_date(element, "entrydate"),
        currency_id=_text(element, "commodity"),
        memo=_text(element, "memo"),
        splits=tuple(splits),
    )

"""Turning a parsed KMyMoney file into a PyMoney book.
The hard part is not the XML, it is one modelling difference.

**KMyMoney stores split amounts as unbounded rationals. PyMoney stores them as
integer minor units.** That is deliberate on both sides: KMyMoney can represent
``1000/3`` exactly, and PyMoney refuses to, because you cannot pay a third of a
cent and a ledger that pretends otherwise will not reconcile against a bank
statement.

The consequence is arithmetic, not philosophy: *summing rounded amounts is not
the same as rounding a summed amount.* Three splits of ``1000/3`` each come to
exactly 1000 in KMyMoney, which displays 1000.00. Booked individually they are
333.33 + 333.33 + 333.33 = 999.99. One cent has evaporated.

So the importer does three things about it:

1. **Measures it.** Every account's exact rational balance is accumulated
   alongside the booked one, so the drift is known per account rather than
   guessed at.
2. **Corrects it.** Where they differ, a correcting transaction is posted
   against a dedicated equity account, so the balance PyMoney shows equals the
   balance KMyMoney showed, to the minor unit.
3. **Says so.** Every correction is in the report, with both figures and the
   number of splits it came from. Nothing is adjusted quietly.

The correction is posted once per account, dated at the end of the ledger. A
balance queried *mid-history* can therefore still be a minor unit away from
KMyMoney's; closing that would need a correction at each point the drift crosses
a unit, which is a lot of noise in the register for a cent. The trade is
documented rather than hidden.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from ...core import commands as cmd
from ...core.account import ACCOUNT_SEPARATOR, Account, AccountType
from ...core.amount import Amount
from ...core.book import Book
from ...core.commodity import Commodity, CommodityKind
from ...core.errors import ConflictError, PyMoneyError
from ...core.posting import LegSpec
from ...core.price import PriceSource
from ...core.rounding import RoundingMode, round_fraction
from .model import (
    ACCOUNT_TYPES,
    NARROWED_TYPES,
    STANDARD_ACCOUNTS,
    KmyAccount,
    KmyCommodity,
    KmyTransaction,
)
from .parser import DEFAULT_MAX_XML_BYTES, KmyFile
from .report import Adjustment, ImportReport, Level

__all__ = ["ImportOptions", "KmyImporter", "import_kmymoney"]

# KMyMoney rounds for display with MyMoneyMoney's "round half away from zero",
# so that is the target to match -- not our own banker's-rounding default. It
# only ever matters for a balance that lands exactly on half a minor unit.
DISPLAY_ROUNDING = RoundingMode.HALF_UP

ADJUSTMENT_PARENT = "KMyMoney Import"


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportOptions:
    """How to handle the things that do not map cleanly."""

    adjust_rounding: bool = True
    """Post correcting entries so balances match KMyMoney. Off: report only."""
    adjustment_date: date | None = None
    """When to date the corrections. Defaults to the last post date in the file."""
    imbalance_account: str = "Imbalance"
    """Where a transaction that does not sum to zero is balanced."""
    opening_account: str = "Opening"
    """Where an opening transaction is stored."""
    rounding_account: str = "Rounding"
    """Where rounding corrections are posted."""
    id_prefix: str = "kmm:"
    """Prefix for ``imported_id``, so a second import of the same file is
    refused rather than silently duplicating the ledger."""
    skip_prices: bool = False
    dry_run: bool = False
    max_xml_bytes: int = DEFAULT_MAX_XML_BYTES
    """How far the file may inflate before the import gives up (gzip bombs)."""
    """Import into a throwaway book and report, without touching the target."""


@dataclass
class _AccountTally:
    """Running comparison between KMyMoney's arithmetic and ours."""

    exact: Fraction = Fraction(0)
    booked: int = 0
    splits: int = 0


class KmyImporter:
    """One import, start to finish."""

    def __init__(
        self,
        book: Book,
        source: KmyFile,
        options: ImportOptions | None = None,
    ) -> None:
        self.book = book
        self.source = source
        self.options = options or ImportOptions()
        self.report = ImportReport(
            source=source.path, book_id=book.id, dry_run=self.options.dry_run
        )
        # KMyMoney id -> what we created
        self.commodities: dict[str, Commodity] = {}
        self.accounts: dict[str, Account] = {}
        self.payees: dict[str, UUID] = {}
        self.tags: dict[str, UUID] = {}
        self.roots: dict[str, Account] = {}
        self._tally: dict[str, _AccountTally] = defaultdict(_AccountTally)
        self._adjustment_accounts: dict[str, Account] = {}
        self._last_post_date: date | None = None
        self._narrowed: set[int] = set()
        self._unmapped: dict[str, int] = defaultdict(int)

    # ====================================================================
    # entry point
    # ====================================================================
    def run(self) -> ImportReport:
        self.report.started_at = self.book.clock.now()
        self.report.revision_before = self.book.revision
        self._refuse_a_repeat_import()

        # One unit of work for the whole import: thousands of commands, one
        # commit, and a failure that escapes leaves no half-imported book. Each
        # command still gets its own savepoint, so one bad transaction is skipped
        # rather than fatal.
        with self.book.bulk():
            self._import_commodities()
            self._choose_base_currency()
            self._import_payees()
            self._import_tags()
            self._import_accounts()
            self._import_transactions()
            if not self.options.skip_prices:
                self._import_prices()
            self._post_rounding_adjustments()
            self._import_schedules()
            self._import_budgets()
            self._close_accounts()

        self._record_skipped()
        self._verify()
        self.report.revision_after = self.book.revision
        self.report.finished_at = self.book.clock.now()
        return self.report

    def _refuse_a_repeat_import(self) -> None:
        """Stop before creating anything if this book has been imported into.

        Found out the hard way, one duplicate transaction at a time, the user
        would be left with a book full of "Current Account (2)". The
        ``imported_id`` prefix makes it a single indexed lookup instead.
        """
        if not self.options.id_prefix:
            return
        existing = self.book.store.find_imported_prefix(self.options.id_prefix)
        if existing is not None:
            raise ConflictError(
                f"this book already holds transactions imported from KMyMoney "
                f"(for example imported_id {existing!r}), so importing again "
                f"would duplicate the ledger. Import into a new book, or delete "
                f"the previous import first."
            )

    # ====================================================================
    # commodities
    # ====================================================================
    def _import_commodities(self) -> None:
        used_codes = {c.key for c in self.book.commodities()}

        for kmy in list(self.source.currencies.values()) + list(
            self.source.securities.values()
        ):
            code = self._unique_code(kmy, used_codes)
            existing = self.book.find_commodity(code)
            if existing is not None:
                # Re-importing into a book that already has EUR: reuse it rather
                # than inventing EUR2.
                self.commodities[kmy.id] = existing
                if existing.minor_unit_factor != kmy.smallest_account_fraction:
                    self.report.warn(
                        "commodity",
                        f"{code} already exists with "
                        f"1/{existing.minor_unit_factor} units; the file says "
                        f"1/{kmy.smallest_account_fraction}. Keeping the "
                        f"existing one, so amounts may be rounded differently "
                        f"than KMyMoney did",
                        source_id=kmy.id,
                    )
                continue

            commodity = self.book.add_commodity(
                code,
                name=kmy.name or code,
                kind=kmy.kind,
                minor_unit_factor=kmy.smallest_account_fraction,
                display_precision=kmy.price_precision if not kmy.is_currency else None,
                symbol=kmy.symbol or None if kmy.is_currency else None,
                isin=kmy.isin or None,
                quote_source=None,
            )
            self.commodities[kmy.id] = commodity
            used_codes.add(commodity.key)
            self.report.count("commodities")
            if kmy.kind is CommodityKind.CURRENCY:
                self.report.count("currencies")
            else:
                self.report.count("securities")

    def _unique_code(self, kmy: KmyCommodity, taken: set[str]) -> str:
        """A code for a commodity, which KMyMoney does not guarantee to have.

        Currencies are ISO codes and always fine. Securities carry a trading
        symbol that may be missing, or duplicated across two instruments on
        different markets.
        """
        candidate = (kmy.symbol or "").strip() or (kmy.id if not kmy.is_currency else "")
        if kmy.is_currency:
            candidate = kmy.id
        if not candidate:
            candidate = kmy.id
        if candidate.upper() not in taken:
            return candidate
        if kmy.is_currency:
            return candidate
        # A duplicate symbol: fall back to the KMyMoney id, which is unique.
        self.report.warn(
            "commodity",
            f"security symbol {candidate!r} is already in use; using the "
            f"KMyMoney id {kmy.id!r} as its code instead",
            source_id=kmy.id,
        )
        return kmy.id

    def _choose_base_currency(self) -> None:
        """Settle the book's reporting currency before any account is created.

        Accounts default to it, so not having one is fatal rather than cosmetic.
        The file usually says; when it does not, guess in descending order of
        reliability and say which rung we landed on.
        """
        chosen = self.commodities.get(self.source.base_currency_id)
        if chosen is None:
            # No kmm-baseCurrency, or it names something we did not import.
            counts: dict[str, int] = defaultdict(int)
            for account in self.source.accounts.values():
                if account.currency_id in self.commodities:
                    counts[account.currency_id] += 1
            if counts:
                chosen = self.commodities[max(counts, key=lambda k: counts[k])]
                self.report.warn(
                    "base currency",
                    f"the file does not name a base currency; using "
                    f"{chosen.code}, the most common account currency",
                )
            else:
                currencies = sorted(
                    (c for c in self.commodities.values() if c.is_currency),
                    key=lambda c: c.code,
                )
                if currencies:
                    chosen = currencies[0]
                    self.report.warn(
                        "base currency",
                        f"the file names no base currency and has no accounts; "
                        f"using {chosen.code}",
                    )
                elif self.book.base_currency_id is not None:
                    return
                else:
                    raise PyMoneyError(
                        f"{self.source.path} contains no currency, so there is "
                        f"nothing to denominate the book in. Import it into a "
                        f"book that already has a base currency."
                    )
        if not chosen.is_currency:
            self.report.warn(
                "base currency",
                f"{chosen.code} is a security, not a currency; leaving the "
                f"book's base currency unchanged",
            )
            return
        if self.book.base_currency_id != chosen.id:
            self.book.set_base_currency(chosen.id)

    def commodity_for(self, reference: str) -> Commodity | None:
        kmy = self.source.commodity(reference)
        if kmy is not None:
            return self.commodities.get(kmy.id)
        return self.commodities.get(reference)

    # ====================================================================
    # payees
    # ====================================================================
    def _import_payees(self) -> None:
        for kmy in sorted(self.source.payees.values(), key=lambda p: p.id):
            name = kmy.name.strip() or f"Unnamed payee {kmy.id}"
            final = name
            suffix = 1
            while self.book.find_payee(final) is not None:
                suffix += 1
                final = f"{name} ({suffix})"
            if final != name:
                self.report.warn(
                    "payee",
                    f"two payees are called {name!r}; the second was imported as "
                    f"{final!r}",
                    source_id=kmy.id,
                )
            notes = "\n".join(p for p in (kmy.notes, kmy.email) if p)
            payee = self.book.add_payee(final, notes=notes, address=kmy.address)
            self.payees[kmy.id] = payee.id
            self.report.count("payees")

    # ====================================================================
    # tags
    # ====================================================================
    def _import_tags(self) -> None:
        """Carry KMyMoney's ``<TAG>`` definitions across as our own tags.

        The file's tag ids (``G000009``) are remembered so the splits that
        reference them can be hooked up in :meth:`_import_transaction`."""
        for tag_id, name in sorted(self.source.tags.items()):
            label = name.strip() or f"Unnamed tag {tag_id}"
            final = label
            suffix = 1
            while self.book.find_tag(final) is not None:
                suffix += 1
                final = f"{label} ({suffix})"
            if final != label:
                self.report.warn(
                    "tag",
                    f"two tags are called {label!r}; the second was imported "
                    f"as {final!r}",
                    source_id=tag_id,
                )
            tag = self.book.add_tag(final)
            self.tags[tag_id] = tag.id
            self.report.count("tags")

    # ====================================================================
    # accounts
    # ====================================================================
    def _import_accounts(self) -> None:
        self._create_roots()
        for kmy in self._accounts_parents_first():
            self._create_account(kmy)

    def _create_roots(self) -> None:
        """The five trees KMyMoney keeps implicit.

        Top-level user accounts name one of these as their parent, but the
        accounts themselves are usually absent from the file, so we create them
        as placeholders. The resulting paths (``Assets:Checking``) differ from
        KMyMoney's display, where the root is a header rather than a node.
        """
        for kmy_id, (default_name, type_) in STANDARD_ACCOUNTS.items():
            declared = self.source.accounts.get(kmy_id)
            declared_name = declared.name.strip() if declared else ""
            # KMyMoney sometimes writes the id as the name (notably files that
            # have been anonymised). Use the default, not "AStd--Asset".
            if declared_name.startswith("AStd::") or ACCOUNT_SEPARATOR in declared_name:
                declared_name = ""
            name = self._clean_name(declared_name or default_name, kmy_id)
            # Importing a second file, or re-running after a failure, should add
            # to the existing trees rather than create "Assets (2)".
            existing = next(
                (a for a in self.book.roots() if a.key == name.casefold()), None
            )
            if existing is not None and existing.account_class is type_.account_class:
                self.roots[kmy_id] = existing
                self.accounts[kmy_id] = existing
                continue
            account = self.book.add_account(name, type_, placeholder=True)
            self.roots[kmy_id] = account
            self.accounts[kmy_id] = account
            self.report.count("accounts")

    def _accounts_parents_first(self) -> list[KmyAccount]:
        """Order accounts so a parent is always created before its children."""
        pending = [a for a in self.source.accounts.values() if not a.is_standard]

        def depth(account: KmyAccount) -> int:
            steps = 0
            current = account
            seen = {current.id}
            while current.parent_id and current.parent_id not in STANDARD_ACCOUNTS:
                parent = self.source.accounts.get(current.parent_id)
                if parent is None or parent.id in seen:
                    break
                seen.add(parent.id)
                current = parent
                steps += 1
                if steps > 100:  # pragma: no cover - a cycle guard
                    break
            return steps

        return sorted(pending, key=lambda a: (depth(a), a.id))

    def _create_account(self, kmy: KmyAccount) -> None:
        parent = self.accounts.get(kmy.parent_id)
        if parent is None:
            parent = self.roots["AStd::Asset"]
            self.report.warn(
                "account",
                f"{kmy.name!r} names parent {kmy.parent_id!r}, which is not in "
                f"the file; filed under {self.book.path(parent)!r}",
                source_id=kmy.id,
            )

        type_ = ACCOUNT_TYPES.get(kmy.type)
        if type_ is None:
            type_ = parent.type if parent.account_class is not None else AccountType.ASSET
            self.report.warn(
                "account",
                f"{kmy.name!r} has unknown KMyMoney type {kmy.type}; imported "
                f"as {type_.value}",
                source_id=kmy.id,
            )
        elif kmy.type in NARROWED_TYPES and kmy.type not in self._narrowed:
            self._narrowed.add(kmy.type)
            self.report.note(
                "account type",
                f"{NARROWED_TYPES[kmy.type]} (PyMoney has no "
                f"separate type); the account class is unchanged",
            )

        # A child must share its parent's class. Where KMyMoney disagrees -- a
        # loan filed under Asset, say -- the parent wins, because the tree is
        # what the user looks at.
        if type_.account_class is not parent.account_class:
            replacement = _class_default(parent.type)
            self.report.warn(
                "account",
                f"{kmy.name!r} is a {type_.value} account under "
                f"{self.book.path(parent)!r}, which is "
                f"{parent.account_class.value}; imported as "
                f"{replacement.value} to keep the tree consistent",
                source_id=kmy.id,
            )
            type_ = replacement

        commodity = self.commodity_for(kmy.currency_id)
        if commodity is None:
            commodity = self.book.base_currency
            self.report.warn(
                "account",
                f"{kmy.name!r} uses commodity {kmy.currency_id!r}, which is not "
                f"in the file; imported in {commodity.code}",
                source_id=kmy.id,
            )

        name = self._clean_name(kmy.name, kmy.id)
        name = self._unique_sibling_name(parent, name, kmy.id)
        institution = self.source.institutions.get(kmy.institution_id)

        account = self.book.add_account(
            name,
            type_,
            parent=parent,
            commodity=commodity,
            description=kmy.description,
            code=kmy.number,
            institution=institution.name if institution else "",
            iban=kmy.iban,
            # Closed accounts are created open and closed at the very end:
            # a closed account rejects postings, and its history still has to
            # be imported.
            placeholder=False,
        )
        self.accounts[kmy.id] = account
        self.report.count("accounts")
        for key in kmy.key_values:
            if key not in ("mm-closed", "iban"):
                self._unmapped[f"account setting {key!r}"] += 1

    def _clean_name(self, name: str, source_id: str) -> str:
        """Make a KMyMoney name usable as a path segment.

        KMyMoney allows a colon in an account name; we use it as the path
        separator, so it has to go.
        """
        cleaned = (name or "").strip()
        if not cleaned:
            cleaned = f"Unnamed {source_id}"
            self.report.warn(
                "account", f"account {source_id} has no name", source_id=source_id
            )
        if ACCOUNT_SEPARATOR in cleaned:
            replaced = cleaned.replace(ACCOUNT_SEPARATOR, "-")
            self.report.warn(
                "account",
                f"{cleaned!r} contains {ACCOUNT_SEPARATOR!r}, which PyMoney uses "
                f"to separate path segments; renamed to {replaced!r}",
                source_id=source_id,
            )
            cleaned = replaced
        return cleaned

    def _unique_sibling_name(self, parent: Account, name: str, source_id: str) -> str:
        existing = {a.key for a in self.book.children(parent.id)}
        if name.casefold() not in existing:
            return name
        suffix = 1
        while True:
            suffix += 1
            candidate = f"{name} ({suffix})"
            if candidate.casefold() not in existing:
                self.report.warn(
                    "account",
                    f"{self.book.path(parent)!r} already has a child called "
                    f"{name!r}; imported as {candidate!r}",
                    source_id=source_id,
                )
                return candidate

    # KMyMoney eRecurrence -> (our Frequency, interval). None = cannot express;
    # an approximation carries a note.
    _RECURRENCE: dict[int, tuple[str, int, bool]] = {
        2: ("DAILY", 1, False),
        4: ("WEEKLY", 1, False),
        8: ("WEEKLY", 2, False),
        16: ("WEEKLY", 2, False),  # fortnightly
        32: ("WEEKLY", 3, False),
        64: ("DAILY", 30, True),  # "every thirty days" ~ monthly
        128: ("MONTHLY", 1, False),
        256: ("WEEKLY", 4, False),
        512: ("WEEKLY", 8, False),
        1024: ("MONTHLY", 2, False),
        2048: ("MONTHLY", 3, False),
        4096: ("MONTHLY", 4, False),
        8192: ("MONTHLY", 6, False),
        16384: ("YEARLY", 1, False),
        32768: ("YEARLY", 2, False),
    }

    def _import_schedules(self) -> None:
        from ...core import commands as cmd
        from ...core.recurrence import Frequency, Recurrence
        from ...core.schedule import ScheduleTemplate, TemplateLeg

        for kmy in self.source.schedules:
            template_txn = kmy.transaction
            when = (template_txn.post_date if template_txn else None) or kmy.start_date
            if template_txn is None or not template_txn.splits or when is None:
                self.report.warn(
                    "schedule",
                    f"{kmy.name!r}: no usable template; skipped",
                    source_id=kmy.id,
                )
                continue
            if kmy.occurrence in (1, 0):  # Once / unknown
                self.report.note(
                    "schedule",
                    f"{kmy.name!r}: a one-off entry; skipped",
                    source_id=kmy.id,
                )
                continue
            mapped = self._RECURRENCE.get(kmy.occurrence)
            if mapped is None or kmy.occurrence == 18:  # 18 = semi-monthly
                self.report.warn(
                    "schedule",
                    f"{kmy.name!r}: its recurrence is not one PyMoney can express; "
                    f"skipped -- re-create it by hand",
                    source_id=kmy.id,
                )
                continue
            freq, interval, approx = mapped
            interval *= max(1, kmy.occurrence_multiplier)

            legs: list[TemplateLeg] = []
            missing = False
            for split in template_txn.splits:
                account = self.accounts.get(split.account_id)
                if account is None:
                    missing = True
                    break
                legs.append(
                    TemplateLeg(
                        account_id=account.id,
                        value=split.value,
                        memo=split.memo,
                        payee_id=(
                            self.payees.get(split.payee_id) if split.payee_id else None
                        ),
                    )
                )
            if missing or len(legs) < 1:
                self.report.warn(
                    "schedule",
                    f"{kmy.name!r}: a template split points at an account that "
                    f"was not imported; skipped",
                    source_id=kmy.id,
                )
                continue

            currency = self.commodity_for(template_txn.currency_id)
            try:
                recurrence = Recurrence(
                    frequency=Frequency(freq),
                    start=when,
                    interval=interval,
                    until=kmy.end_date,
                )
                self.book.execute(
                    cmd.CreateSchedule(
                        name=kmy.name or "Imported schedule",
                        template=ScheduleTemplate(
                            legs=tuple(legs),
                            currency_id=currency.id if currency else None,
                            memo=template_txn.memo,
                            estimate=not kmy.fixed,
                        ),
                        recurrence=recurrence,
                        auto_post=kmy.auto_enter,
                    )
                )
            except Exception as exc:
                self.report.warn(
                    "schedule", f"{kmy.name!r}: {exc}; skipped", source_id=kmy.id
                )
                continue
            self.report.count("schedules")
            if approx:
                self.report.note(
                    "schedule",
                    f"{kmy.name!r}: '{kmy.occurrence}-day' recurrence imported as "
                    f"every {interval} days",
                    source_id=kmy.id,
                )

    def _import_budgets(self) -> None:
        """Bring the plans across.

        KMyMoney holds a budget per year, each spanning twelve months from its
        ``start``, where PyMoney has one plan per account holding whatever
        months carry a figure.  So every budget's figures land in a single
        month-by-month map per account, and that map is the plan -- one figure
        per month, with no default that would run on past the last month the
        file ever planned.
        """
        from ...core.recurrence import add_months

        planned: dict[str, dict[str, Fraction]] = defaultdict(dict)
        treat_as: dict[str, str] = {}
        for kmy in self.source.budgets:
            for entry in kmy.accounts:
                if entry.budget_level in ("", "none") or not entry.account_id:
                    continue
                anchor = kmy.start or next(
                    (p.start for p in entry.periods if p.start is not None), None
                )
                if anchor is None:
                    self.report.warn(
                        "budget",
                        f"{kmy.name!r}: a plan with no start date; skipped",
                        source_id=kmy.id,
                    )
                    continue
                span = []
                for i in range(12):
                    month = add_months(anchor, i)
                    span.append(f"{month.year:04d}-{month.month:02d}")

                if entry.budget_level in ("monthly", "yearly"):
                    if not entry.periods:
                        self.report.warn(
                            "budget",
                            f"{kmy.name!r}: {entry.budget_level} with no figure; skipped",
                            source_id=kmy.id,
                        )
                        continue
                    figure = entry.periods[0].amount
                    if entry.budget_level == "yearly":
                        # KMyMoney's own reports divide the year by twelve too
                        # (pivottable.cpp). Whatever is left over shows up below
                        # as a figure that is not whole minor units.
                        figure = figure / 12
                    planned[entry.account_id].update((ym, figure) for ym in span)
                elif entry.budget_level == "monthbymonth":
                    given = {
                        f"{p.start.year:04d}-{p.start.month:02d}": p.amount
                        for p in entry.periods
                        if p.start is not None
                    }
                    # KMyMoney writes no zero periods, so a month of the span it
                    # leaves out is a month budgeted at nothing.
                    figures = dict(given)
                    for ym in span:
                        figures.setdefault(ym, Fraction(0))
                    planned[entry.account_id].update(figures)
                else:
                    self.report.warn(
                        "budget",
                        f"{kmy.name!r}: budget level {entry.budget_level!r} is not "
                        f"one PyMoney can express; skipped",
                        source_id=kmy.id,
                    )
                    continue
                if entry.treat_as:
                    treat_as[entry.account_id] = entry.treat_as

        for kmy_id, months in planned.items():
            account = self.accounts.get(kmy_id)
            if account is None:
                self.report.warn(
                    "budget",
                    "a plan for an account that was not imported; skipped",
                    source_id=kmy_id,
                )
                self.report.skip("budgets", 1)
                continue
            path = self.book.path(account)
            commodity = account.commodity
            scale = commodity.minor_unit_factor

            written: dict[str, Fraction | str | int | None] = {}
            inexact: list[str] = []
            for ym, figure in months.items():
                scaled = figure * scale
                written[ym] = Fraction(round_fraction(scaled), scale)
                if scaled.denominator != 1:
                    inexact.append(ym)
            if inexact:
                self.report.note(
                    "budget",
                    f"{path!r}: {len(inexact)} planned figure(s) are not whole "
                    f"{commodity.code} minor units; rounded to the nearest",
                    source_id=kmy_id,
                )

            wants = treat_as.get(kmy_id, "")
            if wants and wants != account.account_class.name.lower():
                self.report.note(
                    "budget",
                    f"{path!r}: the plan is marked {wants} in KMyMoney but the "
                    f"account is {account.account_class.name.lower()}; "
                    f"imported on the account's own side",
                    source_id=kmy_id,
                )

            try:
                self.book.execute(
                    cmd.SetBudget(
                        account_id=account.id,
                        months=written,
                        currency_id=commodity.id,
                        replace=True,
                    )
                )
            except Exception as exc:
                self.report.warn("budget", f"{path!r}: {exc}; skipped", source_id=kmy_id)
                self.report.skip("budgets", 1)
                continue
            self.report.count("budgets")

    def _close_accounts(self) -> None:
        for kmy_id, kmy in self.source.accounts.items():
            if not kmy.closed:
                continue
            account = self.accounts.get(kmy_id)
            if account is None:
                continue
            # Children first: closing a parent with open children is refused.
            for candidate in reversed(self.book.subtree(account.id)):
                if not self.book.account(candidate.id).closed:
                    self.book.execute(
                        cmd.SetAccountClosed(account_id=candidate.id, closed=True)
                    )
            self.report.count("closed accounts")

    # ====================================================================
    # transactions
    # ====================================================================
    def _import_transactions(self) -> None:
        for kmy in self.source.iter_transactions():
            try:
                self._import_transaction(kmy)
            except PyMoneyError as exc:
                # The savepoint inside execute() has already undone whatever this
                # transaction wrote, so the batch is intact and we can carry on.
                self.report.error(
                    "transaction",
                    f"could not import: {exc}",
                    source_id=kmy.id,
                )
                self.report.count("failed transactions")

    def _import_transaction(self, kmy: KmyTransaction) -> None:
        if kmy.post_date is None:
            self.report.error(
                "transaction", "has no post date; skipped", source_id=kmy.id
            )
            return
        if not kmy.splits:
            self.report.warn("transaction", "has no splits; skipped", source_id=kmy.id)
            return

        currency = self.commodity_for(kmy.currency_id)
        if currency is None:
            currency = self.book.base_currency
            self.report.warn(
                "transaction",
                f"commodity {kmy.currency_id!r} is not in the file; imported in "
                f"{currency.code}",
                source_id=kmy.id,
            )

        legs: list[LegSpec] = []
        payees: set[UUID] = set()
        for split in kmy.splits:
            account = self.accounts.get(split.account_id)
            if account is None:
                self.report.error(
                    "transaction",
                    f"split {split.id} posts to account {split.account_id!r}, "
                    f"which is not in the file; transaction skipped",
                    source_id=kmy.id,
                )
                return

            payee_id = self.payees.get(split.payee_id) if split.payee_id else None
            if split.payee_id and payee_id is None:
                self.report.warn(
                    "transaction",
                    f"split {split.id} names payee {split.payee_id!r}, which is "
                    f"not in the file",
                    source_id=kmy.id,
                )
            if payee_id is not None:
                payees.add(payee_id)

            same_commodity = account.commodity.id == currency.id
            quantity: Fraction | None = split.shares
            if same_commodity and split.shares != split.value:
                # Should not happen: KMyMoney keeps them equal when the account
                # is in the transaction's currency. The value column is the one
                # that has to balance, so it wins.
                self.report.warn(
                    "transaction",
                    f"split {split.id} is in the transaction currency but has "
                    f"shares {split.shares} and value {split.value}; using the "
                    f"value",
                    source_id=kmy.id,
                )
                quantity = None

            price = self._usable_price(split, currency, account)
            if split.bank_id:
                self._unmapped["split bank id (import reference)"] += 1

            tag_ids: list[UUID] = []
            for source_tag_id in split.tag_ids:
                resolved = self.tags.get(source_tag_id)
                if resolved is None:
                    self.report.warn(
                        "transaction",
                        f"split {split.id} wears tag {source_tag_id!r}, which "
                        f"is not defined in the file",
                        source_id=kmy.id,
                    )
                    continue
                tag_ids.append(resolved)

            legs.append(
                LegSpec(
                    account_id=account.id,
                    value=split.value,
                    quantity=quantity,
                    price=price,
                    memo=split.memo,
                    payee_id=payee_id,
                    action=split.action,
                    number=split.number,
                    reconcile_state=split.reconcile_state,
                    reconcile_date=split.reconcile_date,
                    tag_ids=tuple(tag_ids),
                )
            )
            self.report.count("splits")

        legs = self._balance(legs, kmy, currency)
        if len(legs) == 1:
            if (
                legs[0].action == "Split"
                and legs[0].quantity not in (None, 0)
                and account is not None
            ):
                # A share split: one leg, a change in quantity, no value.
                # 'shares="10/1"' means we have a 10 to one split
                # so if we have x shares we now get 9x shares extra
                shares = self._shares_on_date(
                    account.id, account.commodity.id, kmy.post_date
                )
                new_quantity = (legs[0].quantity - 1) * shares
                legs = [replace(legs[0], action="SPLIT", quantity=new_quantity)]
            elif (
                legs[0].action == "Add"
                and legs[0].quantity not in (None, 0)
                and account is not None
            ):
                # Book against opening balance in correct currency
                price = self._price_on_date(
                    account.commodity.id, currency.id, kmy.post_date
                ) or Fraction(1)
                value = price * legs[0].quantity
                account = self._support_account(self.options.opening_account, currency)
                legs = [replace(legs[0], value=value)]
                legs.append(
                    LegSpec(
                        account_id=account.id,
                        value=-value,
                        memo="KMyMoney importer Add Shares",
                    )
                )
            else:
                self.report.warn(
                    "transaction",
                    "has only one split and nothing to balance it against; skipped",
                    source_id=kmy.id,
                )
                return
        transaction = self.book.post(
            kmy.post_date,
            legs,
            currency=currency,
            memo=kmy.memo,
            payee_id=next(iter(payees)) if len(payees) == 1 else None,
            entered_at=_as_instant(kmy.entry_date, self.book.clock.now()),
            imported_id=f"{self.options.id_prefix}{kmy.id}" if kmy.id else None,
            # The file carries its own price history; a leg whose advisory
            # price we had to drop keeps its booked value and quantity as they
            # are, rather than minting a second, synthetic rate for the day.
            price_foreign_legs=False,
        )
        self.report.count("transactions")

        # Record what KMyMoney's arithmetic said, and what ours booked.
        for split, posted in zip(kmy.splits, transaction.splits, strict=False):
            tally = self._tally[split.account_id]
            if posted.action == "SPLIT":
                # One exception - we assume we have correctly calculated stock splits
                tally.exact += posted.quantity.as_fraction
            else:
                tally.exact += split.shares
            tally.booked += posted.quantity.minor
            tally.splits += 1
        if self._last_post_date is None or kmy.post_date > self._last_post_date:
            self._last_post_date = kmy.post_date

    def _shares_on_date(
        self, account_id: UUID, commodity_id: UUID, when: date
    ) -> Fraction:
        """Return the account's commodity balance immediately before ``when``."""
        balance = Fraction(0)
        for kmy in self.source.iter_transactions():
            if kmy.post_date is None or kmy.post_date >= when:
                continue
            for split in kmy.splits:
                account = self.accounts.get(split.account_id)
                if account is None or account.commodity.id != commodity_id:
                    continue
                if account_id != account.id:
                    continue
                if split.action == "Split" and split.shares not in (None, 0):
                    balance *= split.shares
                else:
                    balance += split.shares
        return balance

    def _price_on_date(
        self, commodity_id: UUID, currency_id: UUID, when: date
    ) -> Fraction | None:
        """Return the most recent recorded price for a commodity on ``when``."""
        source_ids = {
            commodity.id: source_id for source_id, commodity in self.commodities.items()
        }
        commodity_source_id = source_ids.get(commodity_id)
        currency_source_id = source_ids.get(currency_id)
        if commodity_source_id is None or currency_source_id is None:
            return None
        candidates = (
            price
            for price in self.source.prices
            if price.from_id == commodity_source_id
            and price.to_id == currency_source_id
            and price.date <= when
        )
        latest = max(candidates, key=lambda price: price.date, default=None)
        return latest.rate if latest is not None else None

    def _usable_price(self, split, currency, account) -> Fraction | None:
        """KMyMoney's ``price`` attribute is advisory, not a commitment.

        It is meant to equal ``value/shares``, and usually does within the file's
        own rounding. But real files contain split prices that disagree by much
        more than that: anonymisers set it to ``1/1``, legacy imports attach a
        market quote to a split that does not use it, a manual edit leaves it
        behind. The over-specification check in :func:`book_legs` catches that
        in hand-entered data, and it would be wrong to second-guess *there*; but
        the value and quantity columns are the booked numbers, and we are the
        importer, so trusting them here and dropping a price we cannot reconcile
        is the only option that gets the ledger imported.
        """
        price = split.price
        if price is None or price <= 0 or split.shares == 0:
            return None
        # Match book_legs' own tolerance: if we pass a price it would reject as
        # inconsistent, the whole transaction fails. That check assumes both
        # numbers were pre-rounded to the minor unit, which KMyMoney does not
        # do -- it stores unbounded rationals. So for a large foreign-currency
        # value, a perfectly reasonable price can still exceed the tolerance by
        # orders of magnitude. Dropping it there is the honest response: the
        # booked columns are what balance, the price is decorative metadata, and
        # it can be recovered as `split.implied_price` from the stored numbers.
        tolerance = Fraction(1, currency.minor_unit_factor) + price * Fraction(
            1, account.commodity.minor_unit_factor
        )
        if abs(split.value - split.shares * price) > tolerance:
            self._unmapped["split price disagreeing with value/shares"] += 1
            return None
        return price

    def _balance(
        self, legs: list[LegSpec], kmy: KmyTransaction, currency: Commodity
    ) -> list[LegSpec]:
        """Add an imbalance leg if the file's own values do not sum to zero.

        KMyMoney normally guarantees this, so hitting it means the file is odd --
        worth a warning rather than a silent fix, but a fix all the same: an
        unbalanced transaction cannot be stored at all.
        """
        residual = sum((leg.value or Fraction(0) for leg in legs), Fraction(0))
        if residual == 0:
            return legs
        account = self._support_account(self.options.imbalance_account, currency)
        self.report.warn(
            "transaction",
            f"splits sum to {residual} {currency.code} rather than zero; the "
            f"difference was posted to {self.book.path(account)!r}",
            source_id=kmy.id,
        )
        legs.append(
            LegSpec(
                account_id=account.id,
                value=-residual,
                quantity=-residual,
                memo=f"Imbalance from KMyMoney transaction {kmy.id}",
            )
        )
        self.report.count("imbalance splits")
        return legs

    # ====================================================================
    # rounding
    # ====================================================================
    def _post_rounding_adjustments(self) -> None:
        """Make every account's balance match the one KMyMoney displayed."""
        when = (
            self.options.adjustment_date
            or self._last_post_date
            or self.book.clock.today()
        )
        for kmy_id, tally in sorted(self._tally.items()):
            account = self.accounts.get(kmy_id)
            if account is None:  # pragma: no cover - tallies come from postings
                continue
            commodity = account.commodity
            target = round_fraction(
                tally.exact * commodity.minor_unit_factor, DISPLAY_ROUNDING
            )
            delta = target - tally.booked
            if delta == 0:
                continue

            adjustment = Adjustment(
                account_path=self.book.path(account.id),
                commodity_code=commodity.code,
                kmymoney_exact=tally.exact,
                kmymoney_booked=Amount(commodity, target),
                imported=Amount(commodity, tally.booked),
                delta=Amount(commodity, delta),
                split_count=tally.splits,
            )

            if not self.options.adjust_rounding:
                self.report.adjustments.append(adjustment)
                self.report.warn(
                    "rounding",
                    f"{adjustment} (not corrected: adjustments are disabled)",
                    source_id=kmy_id,
                )
                continue

            support = self._support_account(self.options.rounding_account, commodity)
            value = Fraction(delta, commodity.minor_unit_factor)
            transaction = self.book.post(
                when,
                [
                    LegSpec(
                        account_id=account.id,
                        value=value,
                        quantity=value,
                        memo="KMyMoney import: rounding adjustment",
                    ),
                    LegSpec(
                        account_id=support.id,
                        value=-value,
                        quantity=-value,
                        memo=f"Rounding adjustment for {adjustment.account_path}",
                    ),
                ],
                currency=commodity,
                memo=(
                    f"KMyMoney rounding adjustment for {adjustment.account_path} "
                    f"({tally.splits} split(s))"
                ),
            )
            self.report.adjustments.append(
                replace(adjustment, transaction_id=transaction.id)
            )
            self.report.count("rounding adjustments")

    def _support_account(self, leaf: str, commodity: Commodity) -> Account:
        """An equity account to park a correction in, created on demand.

        One per commodity, because a correction has to be in the same commodity
        as the thing it corrects -- otherwise it would need an exchange rate, and
        inventing one to fix a rounding error would be absurd.
        """
        key = f"{leaf}/{commodity.id}"
        cached = self._adjustment_accounts.get(key)
        if cached is not None:
            return cached

        equity_root = self.roots["AStd::Equity"]
        parent = next(
            (
                a
                for a in self.book.children(equity_root.id)
                if a.name == ADJUSTMENT_PARENT
            ),
            None,
        )
        if parent is None:
            parent = self.book.add_account(
                ADJUSTMENT_PARENT,
                AccountType.EQUITY,
                parent=equity_root,
                placeholder=True,
                description="Created by the KMyMoney importer",
            )
            self.report.count("accounts")
        name = f"{leaf} ({commodity.code})"
        account = next((a for a in self.book.children(parent.id) if a.name == name), None)
        if account is None:
            account = self.book.add_account(
                name,
                AccountType.EQUITY,
                parent=parent,
                commodity=commodity,
                description=(
                    "KMyMoney stores split amounts as exact fractions; PyMoney "
                    "books whole minor units. The difference is collected here."
                ),
            )
            self.report.count("accounts")
        self._adjustment_accounts[key] = account
        return account

    # ====================================================================
    # prices
    # ====================================================================
    def _import_prices(self) -> None:
        unresolved: set[str] = set()
        for kmy in self.source.prices:
            base = self.commodity_for(kmy.from_id)
            quote = self.commodity_for(kmy.to_id)
            if base is None or quote is None:
                for reference, resolved in ((kmy.from_id, base), (kmy.to_id, quote)):
                    if resolved is None and reference not in unresolved:
                        unresolved.add(reference)
                        self.report.warn(
                            "price",
                            f"commodity {reference!r} appears in a price but not "
                            f"in the file; its prices were skipped",
                        )
                continue
            if base.id == quote.id:
                continue
            self.book.add_price(
                base,
                quote,
                kmy.rate,
                effective_date=kmy.date,
                # The file records no "when did we learn this", so the honest
                # answer is "at import" (decision #9: as_of is knowledge time).
                as_of=self.book.clock.now(),
                source=kmy.source or PriceSource.IMPORT,
            )
            self.report.count("prices")

    # ====================================================================
    # reporting and verification
    # ====================================================================
    def _record_skipped(self) -> None:
        for name, key in (
            ("saved reports", "reports"),
            ("cost centres", "costcenters"),
            ("online banking jobs", "onlinejobs"),
        ):
            self.report.skip(name, max(self.source.declared_counts.get(key, 0), 0))
        # Schedules and budgets are imported now, and what could not come across
        # is counted where it happens rather than here.  A schedule is counted
        # one-for-one with the file's, so the section's `count` says what is
        # left; a budget is counted per account plan rather than per <BUDGET>
        # element, so that count is a different unit and cannot be subtracted.
        declared = max(self.source.declared_counts.get("schedules", 0), 0)
        self.report.skip(
            "scheduled transactions",
            max(declared - self.report.imported.get("schedules", 0), 0),
        )
        for name, count in sorted(self._unmapped.items()):
            self.report.skip(name, count)

    def _verify(self) -> None:
        """Prove the claim: every balance matches what KMyMoney showed.

        This is the test the user actually cares about, so the importer runs it
        on their data rather than leaving it to ours.
        """
        balances = self.book.balances_by_account()
        checked = 0
        for kmy_id, tally in self._tally.items():
            account = self.accounts.get(kmy_id)
            if account is None:  # pragma: no cover
                continue
            commodity = self.book.account(account.id).commodity
            expected = round_fraction(
                tally.exact * commodity.minor_unit_factor, DISPLAY_ROUNDING
            )
            actual = balances.get(account.id, Amount.zero(commodity)).minor
            checked += 1
            if actual == expected:
                continue
            level = Level.WARNING if not self.options.adjust_rounding else Level.ERROR
            self.report.note(
                "balance check",
                f"{self.book.path(account.id)}: imported balance "
                f"{Amount(commodity, actual).format(symbol=False)} but KMyMoney "
                f"shows {Amount(commodity, expected).format(symbol=False)}",
                source_id=kmy_id,
                level=level,
            )
        self.report.count("balances verified", checked)


def _class_default(parent_type: AccountType) -> AccountType:
    """A safe child type for a parent, when KMyMoney's own type disagrees."""
    return {
        "ASSET": AccountType.ASSET,
        "LIABILITY": AccountType.LIABILITY,
        "INCOME": AccountType.INCOME,
        "EXPENSE": AccountType.EXPENSE,
        "EQUITY": AccountType.EQUITY,
    }[parent_type.account_class.value]


def _as_instant(value: date | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return datetime(value.year, value.month, value.day, tzinfo=fallback.tzinfo)


def import_kmymoney(
    path: Path | str,
    book: Book,
    *,
    options: ImportOptions | None = None,
) -> ImportReport:
    """Import a KMyMoney file into ``book``.

    The book should normally be empty; importing into a populated one works and
    reuses matching commodities, but account and payee names will be
    disambiguated against what is already there.
    """
    options = options or ImportOptions()
    source = KmyFile.read(path, max_bytes=options.max_xml_bytes)
    return KmyImporter(book, source, options).run()

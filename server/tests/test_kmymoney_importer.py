"""Importing a KMyMoney file.

The headline guarantee is in :class:`TestBalancesMatchKMyMoney`: the same
accounts exist, and every balance equals the figure KMyMoney displayed, to the
minor unit. Everything else in this module exists to make that guarantee
trustworthy rather than coincidental.
"""

from __future__ import annotations

import gzip
from collections import defaultdict
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from pymoney.core import (
    AccountClass,
    AccountType,
    Amount,
    Book,
    Commodity,
    CommodityKind,
    ConflictError,
    ManualClock,
    ReconcileState,
    ValuationPolicy,
    balance_sheet,
)
from pymoney.core.rounding import RoundingMode, round_fraction
from pymoney.importers.kmymoney import (
    ImportOptions,
    KmyFile,
    KmyImporter,
    Level,
    import_kmymoney,
)

FIXTURE = Path(__file__).parent / "fixtures" / "household.kmy.xml"
F = Fraction


@pytest.fixture
def source() -> KmyFile:
    return KmyFile.read(FIXTURE)


@pytest.fixture
def imported(store_backend, clock: ManualClock):
    """The fixture file imported into a book on the backend under test."""
    book = store_backend.open(clock=clock)
    report = import_kmymoney(FIXTURE, book)
    return book, report


def account(book: Book, path: str):
    found = book.find_account(path)
    assert found is not None, f"no account at {path!r}; have {_paths(book)}"
    return found


def _paths(book: Book) -> list[str]:
    return sorted(book.path(a) for a in book.accounts())


# ==========================================================================
# the guarantee
# ==========================================================================
class TestBalancesMatchKMyMoney:
    """Every balance, to the cent, against figures computed from the file."""

    @staticmethod
    def kmymoney_balances(source: KmyFile) -> dict[str, Fraction]:
        """KMyMoney's own arithmetic: the exact rational sum of shares.

        Computed here independently of the importer, straight from the file, so
        this is a real check and not the importer agreeing with itself.
        """
        totals: dict[str, Fraction] = defaultdict(Fraction)
        for transaction in source.iter_transactions():
            for split in transaction.splits:
                totals[split.account_id] += split.shares
        return dict(totals)

    def test_every_account_balance_matches(self, imported, source):
        book, report = imported
        expected = self.kmymoney_balances(source)
        assert expected, "the fixture has no postings"

        checked = 0
        for kmy_id, exact in expected.items():
            name = source.accounts[kmy_id].name.replace(":", "-")
            found = next((a for a in book.accounts() if a.name == name), None)
            assert found is not None, f"{name} was not imported"
            factor = found.commodity.minor_unit_factor
            # What KMyMoney displays: its exact total, rounded for display.
            shown = round_fraction(exact * factor, RoundingMode.HALF_UP)
            assert book.balance(found.id).minor == shown, (
                f"{name}: PyMoney has {book.balance(found.id)}, "
                f"KMyMoney shows {Amount(found.commodity, shown)}"
            )
            checked += 1
        assert checked == 10
        assert report.imported["balances verified"] == checked

    def test_the_importer_verified_them_itself(self, imported):
        """The report is the user's evidence, so it must do its own check."""
        _, report = imported
        assert report.ok
        assert not [p for p in report.problems if p.category == "balance check"]

    def test_known_balances(self, imported):
        """Spot-checks with the arithmetic written out, in case the fixture
        changes under us."""
        book, _ = imported
        # 5000.00 opening - 84.20 shop + 3200.00 salary - 10.00 taxi
        # - 3.33 (a third) - 91.74 dollars - 3 x 1.00 = 8007.73
        assert account(book, "Assets:Current Account")
        assert book.balance(account(book, "Assets:Current Account").id).minor == 800773
        assert book.balance(account(book, "Expenses:Shared Taxi").id).minor == 1333
        assert book.balance(account(book, "Expenses:Food-Groceries").id).minor == 8420
        assert book.balance(account(book, "Income:Salary").id).minor == -320000
        assert book.balance(account(book, "Liabilities:Credit Card").id).minor == -450

    def test_double_entry_survives_the_import(self, imported):
        book, _ = imported
        for transaction in book.transactions():
            assert transaction.is_balanced
        assert book.check_integrity() == ()

    def test_the_value_column_nets_to_zero_in_every_currency(self, imported):
        """The global invariant, stated correctly.

        Quantities do *not* net to zero across commodities -- buying 100 USD for
        91.74 EUR moves different amounts of different things, which is the whole
        point of a multi-currency ledger. What must net to zero is the value
        column, per transaction currency.
        """
        book, _ = imported
        totals: dict[str, int] = defaultdict(int)
        for transaction in book.transactions():
            for split in transaction.splits:
                totals[transaction.currency.code] += split.value.minor
        assert totals
        assert all(total == 0 for total in totals.values()), totals


# ==========================================================================
# the rational/minor-unit difference
# ==========================================================================
class TestRoundingAdjustments:
    """KMyMoney keeps exact fractions; we book whole minor units."""

    def test_the_drift_is_detected_and_reported(self, imported):
        _, report = imported
        assert report.adjustments, "the fixture is meant to produce drift"
        by_path = {a.account_path: a for a in report.adjustments}
        wallet = by_path["Assets:Dollar Wallet"]
        # Three purchases of exactly a third of a dollar: 33.33 cents each,
        # which is 99.99 where KMyMoney's exact arithmetic says 100.
        assert wallet.imported.minor == 5099
        assert wallet.kmymoney_booked.minor == 5100
        assert wallet.delta.minor == 1
        assert wallet.kmymoney_exact == F(51)
        assert wallet.split_count == 5

    def test_the_drift_is_corrected(self, imported):
        book, _ = imported
        wallet = account(book, "Assets:Dollar Wallet")
        assert book.balance(wallet.id).minor == 5100

    def test_fractional_shares_drift_too(self, imported):
        """Three reinvestments of a third of a share: 0.9999, not 1.0000."""
        book, report = imported
        by_path = {a.account_path: a for a in report.adjustments}
        apple = by_path["Assets:Brokerage:Apple"]
        assert apple.commodity_code == "AAPL"
        assert apple.imported.minor == 129999
        assert apple.kmymoney_booked.minor == 130000
        assert apple.delta.minor == 1
        assert book.balance(account(book, "Assets:Brokerage:Apple").id).minor == 130000

    def test_the_correction_is_a_real_balanced_transaction(self, imported):
        book, report = imported
        for adjustment in report.adjustments:
            assert adjustment.transaction_id is not None
            transaction = book.transaction(adjustment.transaction_id)
            assert transaction.is_balanced
            assert len(transaction.splits) == 2
            assert "rounding adjustment" in transaction.memo

    def test_corrections_go_to_a_dedicated_equity_account(self, imported):
        """Not to an income or expense account: this is not a real gain."""
        book, _ = imported
        usd = account(book, "Equity:KMyMoney Import:Rounding (USD)")
        assert usd.account_class is AccountClass.EQUITY
        assert usd.commodity.code == "USD"
        assert book.balance(usd.id).minor == -1
        aapl = account(book, "Equity:KMyMoney Import:Rounding (AAPL)")
        assert aapl.commodity.code == "AAPL"

    def test_one_support_account_per_commodity(self, imported):
        """A correction must be in the commodity it corrects; inventing an
        exchange rate to fix a rounding error would be absurd."""
        book, _ = imported
        parent = account(book, "Equity:KMyMoney Import")
        codes = {a.commodity.code for a in book.children(parent.id)}
        assert codes == {"USD", "AAPL"}

    def test_the_zero_sum_allocator_already_absorbs_most_drift(self, imported):
        """Splitting a bill three ways needs no correction at all.

        The value column is booked by the zero-sum allocator, which only floors
        or ceils and never touches a leg that was already exact -- so 10.00 split
        three ways comes to 10.00, not 9.99, without any help.
        """
        book, report = imported
        taxi = account(book, "Expenses:Shared Taxi")
        assert "Expenses:Shared Taxi" not in {a.account_path for a in report.adjustments}
        assert book.balance(taxi.id).minor == 1333

    def test_adjustments_can_be_refused(self, store_backend, clock):
        """Reported but not corrected, for someone who wants the raw numbers."""
        book = store_backend.open(clock=clock)
        report = import_kmymoney(
            FIXTURE, book, options=ImportOptions(adjust_rounding=False)
        )
        assert report.adjustments
        assert all(a.transaction_id is None for a in report.adjustments)
        assert book.balance(account(book, "Assets:Dollar Wallet").id).minor == 5099
        assert book.find_account("Equity:KMyMoney Import") is None
        # Still honest: the mismatch is reported, and as a warning, not a note.
        messages = [p.message for p in report.warnings]
        assert any("not corrected" in m for m in messages)
        assert any(p.category == "balance check" for p in report.warnings)
        assert report.ok

    def test_the_adjustment_date_defaults_to_the_last_posting(self, imported):
        book, report = imported
        last = max(t.date for t in book.transactions())
        for adjustment in report.adjustments:
            assert book.transaction(adjustment.transaction_id).date == last

    def test_the_adjustment_date_can_be_chosen(self, store_backend, clock):
        book = store_backend.open(clock=clock)
        when = date(2026, 6, 30)
        report = import_kmymoney(
            FIXTURE, book, options=ImportOptions(adjustment_date=when)
        )
        for adjustment in report.adjustments:
            assert book.transaction(adjustment.transaction_id).date == when

    def test_the_report_explains_itself(self, imported):
        _, report = imported
        summary = report.summary()
        assert "rounding adjustments: 2 account(s)" in summary
        assert "exact fractions" in summary
        assert "Assets:Dollar Wallet" in summary
        assert report.total_rounding_delta == 2


# ==========================================================================
# structure
# ==========================================================================
class TestAccounts:
    def test_the_five_trees_exist(self, imported):
        book, _ = imported
        for name, klass in (
            ("Assets", AccountClass.ASSET),
            ("Liabilities", AccountClass.LIABILITY),
            ("Income", AccountClass.INCOME),
            ("Expenses", AccountClass.EXPENSE),
            ("Equity", AccountClass.EQUITY),
        ):
            root = account(book, name)
            assert root.parent_id is None
            assert root.account_class is klass
            assert root.placeholder

    def test_the_hierarchy_is_preserved(self, imported):
        book, _ = imported
        apple = account(book, "Assets:Brokerage:Apple")
        assert book.path(apple) == "Assets:Brokerage:Apple"
        assert apple.type is AccountType.STOCK

    def test_account_types_are_mapped(self, imported):
        book, _ = imported
        assert account(book, "Assets:Current Account").type is AccountType.CHECKING
        assert account(book, "Assets:Old Savings").type is AccountType.SAVINGS
        assert account(book, "Assets:Dollar Wallet").type is AccountType.CASH
        assert account(book, "Assets:Brokerage").type is AccountType.INVESTMENT
        assert account(book, "Liabilities:Credit Card").type is AccountType.CREDIT_CARD
        assert account(book, "Income:Salary").type is AccountType.INCOME
        assert account(book, "Equity:Opening Balances").type is AccountType.EQUITY

    def test_metadata_comes_across(self, imported):
        book, _ = imported
        current = account(book, "Assets:Current Account")
        assert current.description == "Everyday banking"
        assert current.code == "12345678"
        assert current.iban == "DE02120300000000202051"
        assert current.institution == "Example Bank"

    def test_a_colon_in_a_name_is_replaced_and_reported(self, imported):
        """We use ':' as the path separator; KMyMoney allows it in a name."""
        book, report = imported
        assert account(book, "Expenses:Food-Groceries")
        assert any(
            "Food:Groceries" in p.message and p.level is Level.WARNING
            for p in report.problems
        )

    def test_closed_accounts_are_closed_after_their_history_is_imported(self, imported):
        """Closed first would reject the postings; closed last is the only order
        that works."""
        book, report = imported
        assert account(book, "Assets:Old Savings").closed
        assert report.imported["closed accounts"] == 1
        assert not account(book, "Assets:Current Account").closed

    def test_a_type_that_would_break_the_tree_is_adjusted(self, imported):
        """A certificate of deposit filed under Expenses: the parent wins,
        because the tree is what the user looks at."""
        book, report = imported
        certificate = account(book, "Expenses:Certificate")
        assert certificate.account_class is AccountClass.EXPENSE
        assert any("keep the tree consistent" in p.message for p in report.warnings)

    def test_a_narrowed_type_is_noted_once(self, imported):
        _, report = imported
        notes = [p for p in report.problems if p.category == "account type"]
        assert len(notes) == 1
        assert "certificate of deposit" in notes[0].message

    def test_an_orphan_account_is_filed_rather_than_lost(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "orphan.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Stray" type="1" '
            'currency="EUR" parentaccount="A999"/></ACCOUNTS>'
            '<TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert account(book, "Assets:Stray")
        assert any("not in the file" in p.message for p in report.warnings)

    def test_duplicate_sibling_names_are_disambiguated(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "dupes.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
            'parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="cash" type="3" currency="EUR" '
            'parentaccount="AStd::Asset"/>'
            "</ACCOUNTS><TRANSACTIONS count="
            '"0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        names = {a.name for a in book.children(account(book, "Assets").id)}
        assert names == {"Cash", "cash (2)"}
        assert any("already has a child" in p.message for p in report.warnings)


class TestCommodities:
    def test_currencies(self, imported):
        book, _ = imported
        eur = book.commodity_by_code("EUR")
        assert eur.name == "Euro"
        assert eur.symbol == "\u20ac"
        assert eur.minor_unit_factor == 100
        assert book.commodity_by_code("JPY").minor_unit_factor == 1

    def test_securities(self, imported):
        book, _ = imported
        aapl = book.commodity_by_code("AAPL")
        assert aapl.kind is CommodityKind.STOCK
        assert aapl.name == "Apple Inc."
        assert aapl.minor_unit_factor == 10000
        assert aapl.isin == "US0378331005"

    def test_the_base_currency_comes_from_the_file(self, imported):
        book, _ = imported
        assert book.base_currency.code == "EUR"

    def test_an_unused_currency_is_still_imported(self, imported):
        """JPY has no account, but a price or a future entry may need it."""
        book, _ = imported
        assert book.find_commodity("JPY") is not None

    def test_a_file_with_no_base_currency_picks_the_commonest(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "nobase.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="2">'
            '<CURRENCY id="EUR" saf="100" name="Euro"/>'
            '<CURRENCY id="USD" saf="100" name="Dollar"/></CURRENCIES>'
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="A" type="3" currency="USD" '
            'parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="B" type="3" currency="USD" '
            'parentaccount="AStd::Asset"/>'
            '</ACCOUNTS><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert book.base_currency.code == "USD"
        assert any("does not name a base currency" in p.message for p in report.warnings)

    def test_securities_with_a_clashing_symbol_fall_back_to_the_id(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "clash.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<SECURITIES count="2">'
            '<SECURITY id="E000001" name="Apple NASDAQ" symbol="AAPL" saf="10000" '
            'type="0"/>'
            '<SECURITY id="E000002" name="Apple Frankfurt" symbol="AAPL" '
            'saf="10000" type="0"/>'
            "</SECURITIES>"
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert book.find_commodity("AAPL") is not None
        assert book.find_commodity("E000002") is not None
        assert any("already in use" in p.message for p in report.warnings)

    def test_importing_into_a_book_that_already_has_the_currency(
        self, store_backend, clock
    ):
        """Reuse EUR rather than inventing a second one."""
        book = store_backend.open(
            clock=clock, base_currency=Commodity.currency("EUR", name="Euro")
        )
        before = book.commodity_by_code("EUR").id
        import_kmymoney(FIXTURE, book)
        assert book.commodity_by_code("EUR").id == before
        assert len([c for c in book.commodities() if c.code == "EUR"]) == 1

    def test_a_conflicting_minor_unit_is_a_warning(self, store_backend, clock):
        """Same code, different smallest unit: amounts would round differently."""
        book = store_backend.open(
            clock=clock, base_currency=Commodity.currency("EUR", scale=3)
        )
        report = import_kmymoney(FIXTURE, book)
        assert any("Keeping the existing one" in p.message for p in report.warnings)


class TestPayees:
    def test_imported(self, imported):
        book, _ = imported
        assert {p.name for p in book.payees()} >= {
            "Supermarket",
            "ACME Corp",
            "Broker Ltd",
        }

    def test_details(self, imported):
        book, _ = imported
        acme = book.find_payee("ACME Corp")
        assert "payroll@acme.invalid" in acme.notes
        assert "Market Row" in book.find_payee("Supermarket").address

    def test_duplicates_are_disambiguated(self, imported):
        """Our book requires unique payee names; KMyMoney does not enforce it."""
        book, report = imported
        assert book.find_payee("Supermarket (2)") is not None
        assert any("two payees are called" in p.message for p in report.warnings)

    def test_attached_to_transactions(self, imported):
        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        assert shop.payee_id == book.find_payee("Supermarket").id
        assert all(s.payee_id == shop.payee_id for s in shop.splits)

    def test_a_payee_that_is_not_in_the_file(self, store_backend, clock, tmp_path):
        path = tmp_path / "ghost.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
            'parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="Food" type="13" currency="EUR" '
            'parentaccount="AStd::Expense"/></ACCOUNTS>'
            '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
            'commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-100/100" value="-100/100" payee="P999"/>'
            '<SPLIT account="A2" shares="100/100" value="100/100"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert any("names payee 'P999'" in p.message for p in report.warnings)
        assert book.balance(account(book, "Assets:Cash").id).minor == -100


class TestTransactions:
    def test_count(self, imported):
        book, report = imported
        # 15 from the file, plus 2 rounding corrections.
        assert len(book.transactions()) == 17
        assert report.imported["transactions"] == 15
        assert report.imported["splits"] == 32

    def test_dates_and_memos(self, imported):
        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        assert shop.date == date(2026, 1, 5)
        assert shop.currency.code == "EUR"

    def test_split_memos_numbers_and_actions(self, imported):
        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        card = shop.splits[0]
        assert card.memo == "card"
        assert card.number == "101"
        buy = next(t for t in book.transactions() if t.memo == "Buy 12 AAPL")
        assert buy.splits[0].action == "Buy"

    def test_reconciliation_state_and_date(self, imported):
        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        card = shop.splits[0]
        assert card.reconcile_state is ReconcileState.RECONCILED
        assert card.reconcile_date == date(2026, 1, 31)
        assert shop.splits[1].reconcile_state is ReconcileState.NOT_RECONCILED
        salary = next(t for t in book.transactions() if t.memo == "January salary")
        assert salary.splits[0].reconcile_state is ReconcileState.CLEARED

    def test_reconciled_transactions_are_locked_on_arrival(self, imported):
        """Decision #11 applies to imported history too."""
        from pymoney.core import ImmutableError
        from pymoney.core import commands as c

        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        assert shop.is_locked
        with pytest.raises(ImmutableError):
            book.execute(c.UpdateTransaction(transaction_id=shop.id, memo="nope"))

    def test_multi_currency_transaction(self, imported):
        book, _ = imported
        fx = next(t for t in book.transactions() if t.memo == "Buy dollars")
        assert fx.currency.code == "EUR"
        usd_leg = fx.split_for(account(book, "Assets:Dollar Wallet").id)
        assert usd_leg.quantity == Amount(book.commodity_by_code("USD"), 10000)
        assert usd_leg.value == Amount(book.commodity_by_code("EUR"), 9174)
        assert usd_leg.price == F(9174, 10000)
        assert fx.is_balanced

    def test_a_share_purchase_keeps_its_price(self, imported):
        """KMyMoney's value is already rounded to cents, so value and
        quantity x price differ slightly -- within tolerance, so the stated
        price is kept."""
        book, _ = imported
        buy = next(t for t in book.transactions() if t.memo == "Buy 12 AAPL")
        shares = buy.split_for(account(book, "Assets:Brokerage:Apple").id)
        assert shares.quantity.minor == 120000
        assert shares.value.minor == 219892
        assert shares.price == F(1832433, 10000)
        assert shares.implied_price != shares.price

    def test_the_three_way_split_keeps_all_four_legs(self, imported):
        book, _ = imported
        taxi = next(t for t in book.transactions() if t.memo == "Taxi split three ways")
        assert len(taxi.splits) == 4
        values = sorted(s.value.minor for s in taxi.splits)
        assert values == [-1000, 333, 333, 334]
        assert sum(values) == 0

    def test_the_source_id_is_recorded(self, imported):
        """So a re-import is refused and a row can be traced back."""
        book, _ = imported
        ids = {t.imported_id for t in book.transactions() if t.imported_id}
        assert "kmm:T000000000000000001" in ids

    def test_a_second_import_of_the_same_file_is_refused(self, imported):
        """And refused *before* anything is created, not one duplicate at a
        time: otherwise the user is left with 'Current Account (2)'."""
        book, _ = imported
        with pytest.raises(ConflictError, match="already holds transactions"):
            import_kmymoney(FIXTURE, book)

    def test_a_refused_reimport_changes_nothing(self, imported):
        book, _ = imported
        before = book.revision
        counts = {a.id: book.balance(a.id) for a in book.accounts()}
        paths = _paths(book)
        with pytest.raises(ConflictError):
            import_kmymoney(FIXTURE, book)
        assert book.revision == before
        assert {a.id: book.balance(a.id) for a in book.accounts()} == counts
        assert _paths(book) == paths

    def test_entered_at_comes_from_the_entry_date(self, imported):
        book, _ = imported
        shop = next(t for t in book.transactions() if t.memo == "Weekly shop")
        assert shop.entered_at.date() == date(2026, 1, 5)


class TestPrices:
    def test_imported(self, imported):
        book, report = imported
        assert report.imported["prices"] == 3
        assert len(book.prices) == 3

    def test_rates_are_exact(self, imported):
        book, _ = imported
        usd = book.commodity_by_code("USD")
        eur = book.commodity_by_code("EUR")
        history = book.prices.history(usd, eur, include_inverse=False)
        assert [p.rate for p in history] == [F(91, 100), F(9174, 10000)]

    def test_sources_are_kept(self, imported):
        book, _ = imported
        assert {p.source for p in book.prices.all()} >= {"KMyMoney", "Yahoo", "User"}

    def test_as_of_is_the_import_time(self, imported, clock):
        """The file records no "when did we learn this", so the honest answer is
        "at import" (decision #9)."""
        book, _ = imported
        assert all(p.as_of == clock.now() for p in book.prices.all())

    def test_a_security_priced_by_id_resolves(self, imported):
        book, _ = imported
        aapl = book.commodity_by_code("AAPL")
        usd = book.commodity_by_code("USD")
        assert book.prices.history(aapl, usd, include_inverse=False)

    def test_they_make_a_balance_sheet_possible(self, imported):
        """The point of importing prices: a multi-currency report that works."""
        book, _ = imported
        sheet = balance_sheet(
            book,
            ValuationPolicy(target=book.base_currency, as_of=date(2026, 1, 31)),
        )
        assert sheet.assets.minor > 0
        assert sheet.unpriced == ()

    def test_prices_can_be_skipped(self, store_backend, clock):
        book = store_backend.open(clock=clock)
        import_kmymoney(FIXTURE, book, options=ImportOptions(skip_prices=True))
        assert len(book.prices) == 0

    def test_a_price_naming_an_unknown_commodity(self, store_backend, clock, tmp_path):
        path = tmp_path / "badprice.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/>'
            '<PRICES count="1"><PRICEPAIR from="XAU" to="EUR">'
            '<PRICE date="2026-01-01" price="2000/1"/></PRICEPAIR></PRICES>'
            "</KMYMONEY-FILE>"
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert len(book.prices) == 0
        assert any("'XAU'" in p.message for p in report.warnings)


# ==========================================================================
# reporting
# ==========================================================================
class TestReport:
    def test_scheduled_transactions_are_imported(self, imported):
        book, report = imported
        assert report.imported["schedules"] == 2
        assert "scheduled transactions" not in report.skipped  # none left behind
        by_name = {s.name: s for s in book.schedules()}
        assert by_name["Rent"].recurrence.describe() == "every 3 weeks on Sunday"
        assert by_name["Rent"].next_due.isoformat() == "2026-02-01"
        assert by_name["Rent"].auto_post is True
        assert by_name["Insurance"].recurrence.describe() == "every 2 months on day 1"
        assert by_name["Insurance"].auto_post is False

    def test_nothing_is_dropped_silently(self, imported):
        _, report = imported
        # Budgets came across in one piece, so they are not among the things
        # left behind.
        assert "budgets" not in report.skipped
        assert report.skipped["split bank id (import reference)"] == 1
        assert any("account setting" in key for key in report.skipped)

    def test_tags_are_imported_not_skipped(self, imported):
        book, report = imported
        assert report.imported["tags"] == 2
        assert "tags" not in report.skipped and "split tags" not in report.skipped
        assert {t.name for t in book.tags()} == {"holiday", "work"}

    def test_counts(self, imported):
        _, report = imported
        assert report.imported["accounts"] == 20  # 5 roots + 12 + 3 support
        assert report.imported["payees"] == 4
        assert report.imported["currencies"] == 3
        assert report.imported["securities"] == 1
        assert report.imported["budgets"] == 3

    def test_the_plans_come_across(self, imported):
        """The three shapes the fixture holds: a flat allowance, a month-by-
        month one whose later months the file never mentions, and an income
        target.  Each is stored as the months it covers, and no further."""
        book, _ = imported
        plans = {book.path(book.account(b.account_id)): b for b in book.budgets()}
        year = {f"2026-{m:02d}": 300000 for m in range(1, 13)}

        assert plans["Income:Salary"].months == year
        assert plans["Expenses:Shared Taxi"].months == {
            f"2026-{m:02d}": 40000 for m in range(1, 13)
        }

        # KMyMoney writes no zero periods, so the months the file leaves out
        # are months it planned at nothing: an explicit 0, not an absent entry.
        # Neither figure runs on into 2027.
        charges = plans["Expenses:Bank Charges"]
        assert charges.months == {
            "2026-01": 10000,
            "2026-02": 15000,
            **{f"2026-{m:02d}": 0 for m in range(3, 13)},
        }
        assert charges.for_month("2027-01") is None

    def test_revisions_and_timing(self, imported):
        book, report = imported
        assert report.revision_before < report.revision_after == book.revision
        assert report.duration_seconds is not None
        assert report.book_id == book.id

    def test_summary_is_readable(self, imported):
        _, report = imported
        summary = report.summary()
        assert "Imported" in summary
        assert "household.kmy.xml" in summary
        assert "result: OK" in summary
        assert "not imported" in summary
        # Notes are hidden unless asked for, warnings never are.
        assert "use --verbose" in summary
        assert "WARNING" in summary

    def test_verbose_shows_the_notes(self, imported):
        _, report = imported
        assert "certificate of deposit" in report.summary(verbose=True)

    def test_str_is_the_summary(self, imported):
        _, report = imported
        assert str(report) == report.summary()

    def test_problem_formatting(self, imported):
        _, report = imported
        problem = report.warnings[0]
        assert problem.level.value in str(problem)
        assert problem.source_id in str(problem)


class TestFailureModes:
    """One bad row must not cost the user the other ten years."""

    def _book(self, store_backend, clock) -> Book:
        return store_backend.open(clock=clock)

    def test_a_transaction_posting_to_a_missing_account(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "missing.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
            'parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="Food" type="13" currency="EUR" '
            'parentaccount="AStd::Expense"/></ACCOUNTS>'
            '<TRANSACTIONS count="2">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
            '<SPLIT account="A999" shares="100/100" value="100/100"/>'
            "</SPLITS></TRANSACTION>"
            '<TRANSACTION id="T2" postdate="2026-01-02" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-200/100" value="-200/100"/>'
            '<SPLIT account="A2" shares="200/100" value="200/100"/>'
            "</SPLITS></TRANSACTION>"
            "</TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = self._book(store_backend, clock)
        report = import_kmymoney(path, book)
        # The good one survived; the bad one is an error, not a crash.
        assert book.balance(account(book, "Assets:Cash").id).minor == -200
        assert len(book.transactions()) == 1
        assert any("A999" in p.message for p in report.errors)
        assert not report.ok

    def test_an_unbalanced_transaction_is_balanced_and_flagged(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "unbalanced.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
            'parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="Food" type="13" currency="EUR" '
            'parentaccount="AStd::Expense"/></ACCOUNTS>'
            '<TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-1000/100" value="-1000/100"/>'
            '<SPLIT account="A2" shares="900/100" value="900/100"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = self._book(store_backend, clock)
        report = import_kmymoney(path, book)
        imbalance = account(book, "Equity:KMyMoney Import:Imbalance (EUR)")
        assert book.balance(imbalance.id).minor == 100
        assert any("rather than zero" in p.message for p in report.warnings)
        assert book.check_integrity() == ()

    def test_a_transaction_with_no_post_date_is_skipped(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "nodate.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Cash" type="3" '
            'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
            '<TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = self._book(store_backend, clock)
        report = import_kmymoney(path, book)
        assert book.transactions() == ()
        assert any("no post date" in p.message for p in report.errors)

    def test_a_single_split_transaction_is_balanced_to_imbalance(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "single.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Cash" type="3" '
            'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
            '<TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = self._book(store_backend, clock)
        report = import_kmymoney(path, book)
        assert book.balance(account(book, "Assets:Cash").id).minor == -100
        imbalance = account(book, "Equity:KMyMoney Import:Imbalance (EUR)")
        assert book.balance(imbalance.id).minor == 100
        assert report.imported["imbalance splits"] == 1

    def test_an_empty_file_imports_nothing_and_succeeds(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "fresh.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = self._book(store_backend, clock)
        report = import_kmymoney(path, book)
        assert report.ok
        assert report.adjustments == []
        assert len(book.accounts()) == 5  # just the roots
        assert book.check_integrity() == ()


class TestGzippedFile:
    def test_the_real_thing(self, store_backend, clock, tmp_path):
        """KMyMoney writes gzip by default, so that is the normal path."""
        target = tmp_path / "household.kmy"
        target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
        book = store_backend.open(clock=clock)
        report = import_kmymoney(target, book)
        assert report.ok
        assert book.balance(account(book, "Assets:Current Account").id).minor == 800773
        assert book.balance(account(book, "Assets:Dollar Wallet").id).minor == 5100


def write(path: Path, *, body: str, currencies: str | None = None) -> Path:
    """A minimal file with the boilerplate filled in."""
    if currencies is None:
        currencies = '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/></CURRENCIES>'
    path.write_text(
        '<?xml version="1.0"?><KMYMONEY-FILE>'
        '<KEYVALUEPAIRS><PAIR key="kmm-baseCurrency" value="EUR"/></KEYVALUEPAIRS>'
        f"{currencies}{body}</KMYMONEY-FILE>"
    )
    return path


CASH_AND_FOOD = (
    '<ACCOUNTS count="2">'
    '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" parentaccount="AStd::Asset"/>'
    '<ACCOUNT id="A2" name="Food" type="13" currency="EUR" parentaccount="AStd::Expense"/>'
    "</ACCOUNTS>"
)


class TestBudgetImport:
    """The plan comes across or says why not -- never half of it.

    A plan is its months: every month the file covers lands as an explicit
    figure, and nothing runs on past the last one.  So the shapes below are
    written as whole spans rather than as a default plus exceptions.
    """

    ACCOUNTS = (
        '<ACCOUNTS count="3">'
        '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" parentaccount="AStd::Asset"/>'
        '<ACCOUNT id="A2" name="Groceries" type="13" currency="EUR" parentaccount="AStd::Expense"/>'
        '<ACCOUNT id="A3" name="Salary" type="12" currency="EUR" parentaccount="AStd::Income"/>'
        "</ACCOUNTS>"
    )

    def _plan(
        self, tmp_path, store_backend, clock, body, *, accounts=None, currencies=None
    ):
        book = store_backend.open(clock=clock)
        report = import_kmymoney(
            write(
                tmp_path / "plans.kmy",
                body=(accounts or self.ACCOUNTS) + body,
                currencies=currencies,
            ),
            book,
        )
        return book, report

    @staticmethod
    def _span(figure: int, first: str, count: int) -> dict[str, int]:
        """`figure` in minor units for `count` months from `first`."""
        year, month = (int(part) for part in first.split("-"))
        out: dict[str, int] = {}
        for i in range(count):
            out[f"{year + (month - 1 + i) // 12:04d}-{(month - 1 + i) % 12 + 1:02d}"] = (
                figure
            )
        return out

    def test_a_monthly_plan_fills_the_year_it_is_for(
        self, tmp_path, store_backend, clock
    ):
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="monthly" budgetsubaccounts="0">'
            '<PERIOD amount="400/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        (plan,) = book.budgets()
        assert plan.months == self._span(40000, "2026-01", 12)
        # Nothing runs on: the file planned a year, so 2027 is unanswered.
        assert plan.for_month("2027-01") is None
        assert report.imported["budgets"] == 1

    def test_a_month_by_month_plan_zero_fills_the_months_it_omits(
        self, tmp_path, store_backend, clock
    ):
        book, _ = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="monthbymonth" budgetsubaccounts="0">'
            '<PERIOD amount="100/1" start="2026-01-01"/>'
            '<PERIOD amount="301/2" start="2026-02-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        (plan,) = book.budgets()
        # KMyMoney writes no zero periods, so a month it leaves out is a month
        # it planned at nothing -- an explicit 0, not an absent entry.
        assert plan.months == {
            "2026-01": 10000,
            "2026-02": 15050,
            **{f"2026-{m:02d}": 0 for m in range(3, 13)},
        }

    def test_a_yearly_plan_divides_into_twelve_months(
        self, tmp_path, store_backend, clock
    ):
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="yearly" budgetsubaccounts="0">'
            '<PERIOD amount="1200/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        (plan,) = book.budgets()
        assert plan.months == self._span(10000, "2026-01", 12)  # 1200.00 / 12
        assert not report.warnings

    def test_a_yearly_plan_that_does_not_divide_says_so(
        self, tmp_path, store_backend, clock
    ):
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="yearly" budgetsubaccounts="0">'
            '<PERIOD amount="100/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        (plan,) = book.budgets()
        assert plan.months == self._span(833, "2026-01", 12)  # 100.00 / 12, to the cent
        assert any("minor units" in p.message for p in report.problems)

    def test_several_budgets_lie_side_by_side(self, tmp_path, store_backend, clock):
        """A KMyMoney file holds one budget per year; PyMoney holds the months.
        So two years at two figures are two runs of months, and neither figure
        applies to months the other year already answered."""
        book, _ = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="2">'
            '<BUDGET id="B1" name="2025" start="2025-01-01">'
            '<ACCOUNT id="A2" budgetlevel="monthly" budgetsubaccounts="0">'
            '<PERIOD amount="400/1" start="2025-01-01"/>'
            "</ACCOUNT></BUDGET>"
            '<BUDGET id="B2" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="monthly" budgetsubaccounts="0">'
            '<PERIOD amount="450/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        (plan,) = book.budgets()
        assert plan.months == {
            **self._span(40000, "2025-01", 12),
            **self._span(45000, "2026-01", 12),
        }
        assert plan.for_month("2027-01") is None

    def test_a_plan_for_an_account_that_was_not_imported_is_reported(
        self, tmp_path, store_backend, clock
    ):
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A999" budgetlevel="monthly" budgetsubaccounts="0">'
            '<PERIOD amount="400/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        assert book.budgets() == ()
        assert report.skipped["budgets"] == 1
        assert any("not imported" in p.message for p in report.warnings)

    def test_a_plan_on_a_root_is_refused_and_said_so(
        self, tmp_path, store_backend, clock
    ):
        """The five KMyMoney roots become placeholders, and a placeholder
        groups its children rather than taking a plan of its own."""
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="AStd::Expense" budgetlevel="monthly" budgetsubaccounts="1">'
            '<PERIOD amount="400/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        assert book.budgets() == ()
        assert report.skipped["budgets"] == 1
        assert any("placeholder" in p.message for p in report.warnings)

    def test_a_treat_as_that_disagrees_with_the_account_is_reported(
        self, tmp_path, store_backend, clock
    ):
        _, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A2" budgetlevel="monthly" budgetsubaccounts="0" type="income">'
            '<PERIOD amount="400/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
        )
        assert any("marked income" in p.message for p in report.problems)

    def test_a_plan_lands_in_the_accounts_own_currency(
        self, tmp_path, store_backend, clock
    ):
        currencies = (
            '<CURRENCIES count="2">'
            '<CURRENCY id="EUR" saf="100" name="Euro"/>'
            '<CURRENCY id="USD" saf="100" name="US Dollar"/>'
            "</CURRENCIES>"
        )
        book, _ = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01">'
            '<ACCOUNT id="A4" budgetlevel="monthly" budgetsubaccounts="0">'
            '<PERIOD amount="50/1" start="2026-01-01"/>'
            "</ACCOUNT></BUDGET></BUDGETS>",
            accounts=(
                '<ACCOUNTS count="1">'
                '<ACCOUNT id="A4" name="Wallet" type="3" currency="USD" '
                'parentaccount="AStd::Asset"/>'
                "</ACCOUNTS>"
            ),
            currencies=currencies,
        )
        (plan,) = book.budgets()
        assert plan.currency.code == "USD"
        assert plan.months == self._span(5000, "2026-01", 12)

    def test_a_budget_with_no_accounts_imports_nothing(
        self, tmp_path, store_backend, clock
    ):
        book, report = self._plan(
            tmp_path,
            store_backend,
            clock,
            '<BUDGETS count="1"><BUDGET id="B1" name="2026" start="2026-01-01"/>'
            "</BUDGETS>",
        )
        assert book.budgets() == ()
        assert "budgets" not in report.imported
        assert "budgets" not in report.skipped


class TestAwkwardFiles:
    """The cases a decade-old file actually contains."""

    def test_no_currencies_at_all_needs_a_book_that_has_one(
        self, store_backend, clock, tmp_path
    ):
        path = tmp_path / "nocur.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><ACCOUNTS count="0"/>'
            '<TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock, base_currency=Commodity.currency("CHF"))
        report = import_kmymoney(path, book)
        assert report.ok
        assert book.base_currency.code == "CHF"

    def test_no_currencies_and_no_book_currency_is_fatal(
        self, store_backend, clock, tmp_path
    ):
        """Better to refuse than to invent a currency for someone's ledger."""
        from pymoney.core import PyMoneyError

        path = tmp_path / "nocur2.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><ACCOUNTS count="0"/>'
            '<TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        with pytest.raises(PyMoneyError, match="nothing to denominate"):
            import_kmymoney(path, book)

    def test_a_base_currency_that_is_a_security(self, store_backend, clock, tmp_path):
        path = tmp_path / "secbase.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<KEYVALUEPAIRS><PAIR key="kmm-baseCurrency" value="E000001"/>'
            "</KEYVALUEPAIRS>"
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<SECURITIES count="1"><SECURITY id="E000001" name="Apple" '
            'symbol="AAPL" saf="10000" type="0"/></SECURITIES>'
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock, base_currency=Commodity.currency("CHF"))
        report = import_kmymoney(path, book)
        assert book.base_currency.code == "CHF"
        assert any("is a security, not a currency" in p.message for p in report.warnings)

    def test_an_unknown_account_type(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "type0.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Mystery" type="0" '
                'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert account(book, "Assets:Mystery").account_class is AccountClass.ASSET
        assert any("unknown KMyMoney type 0" in p.message for p in report.warnings)

    def test_an_account_with_no_name(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "noname.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="A1" name="" type="3" '
                'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert account(book, "Assets:Unnamed A1")
        assert any("has no name" in p.message for p in report.warnings)

    def test_a_payee_with_no_name(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "nopayee.xml",
            body=(
                '<PAYEES count="1"><PAYEE id="P1" name=""/></PAYEES>'
                '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(path, book)
        assert book.find_payee("Unnamed payee P1") is not None

    def test_a_transaction_with_no_splits(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "nosplits.xml",
            body=(
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="EUR"/></TRANSACTIONS>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert book.transactions() == ()
        assert any("no splits" in p.message for p in report.warnings)

    def test_a_transaction_in_an_unknown_currency(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "badcur.xml",
            body=(
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="XYZ"><SPLITS>'
                '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
                '<SPLIT account="A2" shares="100/100" value="100/100"/>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert book.transactions()[0].currency.code == "EUR"
        assert any("'XYZ' is not in the file" in p.message for p in report.warnings)

    def test_shares_disagreeing_with_value_in_the_same_currency(
        self, store_backend, clock, tmp_path
    ):
        """A contradictory file: the value column wins, then the balance is
        corrected back to what KMyMoney's register showed.

        Worth spelling out, because it shows the correction machinery is not
        only about rounding: it closes *any* gap between the balance KMyMoney
        displays (the sum of shares) and what we were able to book.
        """
        path = write(
            tmp_path / "disagree.xml",
            body=(
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-90/100" value="-100/100"/>'
                '<SPLIT account="A2" shares="100/100" value="100/100"/>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert any("using the value" in p.message for p in report.warnings)
        # The transaction balances on the value column...
        assert book.transactions()[0].is_balanced
        # ...and the account ends up showing what KMyMoney showed.
        assert book.balance(account(book, "Assets:Cash").id).minor == -90
        assert [a.delta.minor for a in report.adjustments] == [10]
        assert report.ok

    def test_a_nonsensical_price_is_dropped(self, store_backend, clock, tmp_path):
        path = write(
            tmp_path / "zeroprice.xml",
            body=(
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-100/100" value="-100/100" price="0/1"/>'
                '<SPLIT account="A2" shares="0/1" value="0/1" price="5/1"/>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(path, book)
        transaction = book.transactions()[0]
        assert all(s.price is None for s in transaction.splits)

    def test_tags_and_their_split_links_are_imported(
        self, store_backend, clock, tmp_path
    ):
        """The example from the feature request: a tag defined at the top of
        the file, worn by one leg of one transaction."""
        path = write(
            tmp_path / "tagged.xml",
            body=(
                '<TAGS count="2">'
                '<TAG id="G000009" name="holiday"/>'
                '<TAG id="G000010" name="work"/>'
                "</TAGS>"
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T000000000000004495" '
                'postdate="2026-05-18" commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-4750/1" value="-4750/1"/>'
                '<SPLIT account="A2" shares="4750/1" value="4750/1">'
                '<TAG id="G000009"/></SPLIT>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert report.imported["tags"] == 2
        holiday = book.find_tag("holiday")
        assert holiday is not None
        (transaction,) = book.transactions()
        spent, categorised = transaction.splits
        assert spent.tag_ids == ()
        assert categorised.tag_ids == (holiday.id,)
        assert "tags" not in report.skipped
        assert "split tags" not in report.skipped

    def test_a_split_naming_an_undefined_tag_is_warned_not_fatal(
        self, store_backend, clock, tmp_path
    ):
        path = write(
            tmp_path / "dangling-tag.xml",
            body=(
                '<TAGS count="0"/>'
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
                '<SPLIT account="A2" shares="100/100" value="100/100">'
                '<TAG id="G404"/></SPLIT>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert report.imported["transactions"] == 1
        assert all(s.tag_ids == () for s in book.transactions()[0].splits)
        assert any("G404" in p.message for p in report.warnings)

    def test_a_lone_zero_split_cannot_be_balanced(self, store_backend, clock, tmp_path):
        """Nothing to post against, and nothing to balance: skip and say so."""
        path = write(
            tmp_path / "lonezero.xml",
            body=(
                f"{CASH_AND_FOOD}"
                '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01" '
                'commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="0/1" value="0/1"/>'
                "</SPLITS></TRANSACTION></TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert book.transactions() == ()
        assert any("only one split" in p.message for p in report.warnings)

    def test_two_accounts_drifting_in_the_same_commodity_share_one_support(
        self, store_backend, clock, tmp_path
    ):
        # Three thirds of a share each: 0.9999 booked where KMyMoney says 1.
        entries = "".join(
            f'<TRANSACTION id="T{account_id}{n}" postdate="2026-01-0{n + 1}" '
            f'commodity="EUR"><SPLITS>'
            f'<SPLIT account="{account_id}" shares="10000/30000" value="1000/100" '
            f'price="30/1"/>'
            f'<SPLIT account="A1" shares="-1000/100" value="-1000/100"/>'
            f"</SPLITS></TRANSACTION>"
            for account_id in ("A2", "A3")
            for n in range(3)
        )
        path = write(
            tmp_path / "twodrift.xml",
            body=(
                '<ACCOUNTS count="3">'
                '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
                'parentaccount="AStd::Asset"/>'
                '<ACCOUNT id="A2" name="Shares A" type="15" currency="E1" '
                'parentaccount="AStd::Asset"/>'
                '<ACCOUNT id="A3" name="Shares B" type="15" currency="E1" '
                'parentaccount="AStd::Asset"/>'
                "</ACCOUNTS>"
                f'<TRANSACTIONS count="6">{entries}</TRANSACTIONS>'
                '<SECURITIES count="1"><SECURITY id="E1" name="Thing" symbol="THG" '
                'saf="10000" type="0"/></SECURITIES>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert len(report.adjustments) == 2
        support = account(book, "Equity:KMyMoney Import:Rounding (THG)")
        # One support account, not one per drifting account.
        parent = account(book, "Equity:KMyMoney Import")
        assert len(book.children(parent.id)) == 1
        assert book.balance(support.id).minor == -2

    def test_two_files_into_one_book_reuse_the_trees(
        self, store_backend, clock, tmp_path
    ):
        """The roots must be reused, or the second import creates 'Assets (2)'."""
        first = write(
            tmp_path / "one.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Cash" type="3" '
                'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        second = write(
            tmp_path / "two.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="A9" name="Wallet" type="3" '
                'currency="EUR" parentaccount="AStd::Asset"/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(first, book)
        import_kmymoney(second, book)
        assert [a.name for a in book.roots()] == [
            "Assets",
            "Equity",
            "Expenses",
            "Income",
            "Liabilities",
        ]
        assert account(book, "Assets:Cash")
        assert account(book, "Assets:Wallet")

    def test_a_standard_account_named_in_the_file_keeps_its_name(
        self, store_backend, clock, tmp_path
    ):
        path = write(
            tmp_path / "named.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="AStd::Asset" name="Vermogen" '
                'type="9" currency="EUR" parentaccount=""/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(path, book)
        assert account(book, "Vermogen").placeholder
        assert book.find_account("Assets") is None

    def test_one_unpostable_transaction_does_not_cost_the_others(
        self, store_backend, clock, tmp_path
    ):
        """The whole point of a savepoint per command.

        A split that gains 2.00 EUR of value while *losing* 3.00 USD of shares is
        rejected by the domain, so this one cannot be stored -- but the nine good
        years around it must survive, reported rather than lost.
        """
        path = write(
            tmp_path / "signs.xml",
            body=(
                '<ACCOUNTS count="3">'
                '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR" '
                'parentaccount="AStd::Asset"/>'
                '<ACCOUNT id="A2" name="Wallet" type="3" currency="USD" '
                'parentaccount="AStd::Asset"/>'
                '<ACCOUNT id="A3" name="Food" type="13" currency="EUR" '
                'parentaccount="AStd::Expense"/>'
                "</ACCOUNTS>"
                '<TRANSACTIONS count="2">'
                '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-100/100" value="-100/100"/>'
                '<SPLIT account="A3" shares="100/100" value="100/100"/>'
                "</SPLITS></TRANSACTION>"
                '<TRANSACTION id="T2" postdate="2026-01-02" commodity="EUR"><SPLITS>'
                '<SPLIT account="A1" shares="-200/100" value="-200/100"/>'
                '<SPLIT account="A2" shares="-300/100" value="200/100"/>'
                "</SPLITS></TRANSACTION>"
                "</TRANSACTIONS>"
            ),
            currencies=(
                '<CURRENCIES count="2">'
                '<CURRENCY id="EUR" saf="100" name="Euro"/>'
                '<CURRENCY id="USD" saf="100" name="Dollar"/></CURRENCIES>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert len(book.transactions()) == 1
        assert book.balance(account(book, "Assets:Cash").id).minor == -100
        assert report.imported["failed transactions"] == 1
        assert any("could not import" in p.message for p in report.errors)
        assert not report.ok
        assert book.check_integrity() == ()

    def test_an_account_in_a_commodity_that_is_not_in_the_file(
        self, store_backend, clock, tmp_path
    ):
        path = write(
            tmp_path / "ghostcur.xml",
            body=(
                '<ACCOUNTS count="1"><ACCOUNT id="A1" name="Gold" type="9" '
                'currency="XAU" parentaccount="AStd::Asset"/></ACCOUNTS>'
                '<TRANSACTIONS count="0"/>'
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert account(book, "Assets:Gold").commodity.code == "EUR"
        assert any("uses commodity 'XAU'" in p.message for p in report.warnings)

    def test_rows_without_ids_are_ignored(self, store_backend, clock, tmp_path):
        """A hand-edited file should lose the broken row, not the import."""
        path = tmp_path / "noids.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="2"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            '<CURRENCY name="No id"/></CURRENCIES>'
            '<SECURITIES count="1"><SECURITY name="No id" saf="10000"/></SECURITIES>'
            '<PAYEES count="1"><PAYEE name="No id"/></PAYEES>'
            '<INSTITUTIONS count="1"><INSTITUTION name="No id"/></INSTITUTIONS>'
            '<TAGS count="1"><TAG name="No id"/></TAGS>'
            '<ACCOUNTS count="1"><ACCOUNT name="No id" type="3"/></ACCOUNTS>'
            '<TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert report.ok
        assert [c.code for c in book.commodities()] == ["EUR"]
        assert book.payees() == ()
        assert len(book.accounts()) == 5

    def test_an_unparseable_price_precision(self, store_backend, clock, tmp_path):
        path = tmp_path / "ppp.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1">'
            '<CURRENCY id="EUR" saf="100" name="Euro" ppp="lots"/></CURRENCIES>'
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(path, book)
        assert book.commodity_by_code("EUR").precision == 2


class TestFieldTestRegressions:
    """Bugs that a real KMyMoney file surfaced; these keep them fixed."""

    def test_last_modified_with_a_full_iso_timestamp(self, tmp_path):
        """Newer KMyMoney writes the timestamp, not just the date."""
        from pymoney.importers.kmymoney import KmyFile

        path = tmp_path / "dated.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<FILEINFO><LAST_MODIFIED_DATE date="2026-08-22T08:23:17+02:00"/>'
            "</FILEINFO></KMYMONEY-FILE>"
        )
        assert KmyFile.read(path).info.last_modified == date(2026, 8, 22)

    def test_anonymiser_wrote_the_id_as_the_root_name(
        self, store_backend, clock, tmp_path
    ):
        """The anonymiser emitted ``<ACCOUNT id="AStd::Asset" name="AStd::Asset">``,
        and colon-stripping faithfully produced ``AStd--Asset`` as the root.

        A file with that shape must still come out with the normal names, so
        the imported book's paths read as KMyMoney's own UI would show them.
        """
        path = tmp_path / "anon.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="100" name="Euro"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="AStd::Asset" name="AStd::Asset" type="9" currency="EUR"'
            ' parentaccount=""/>'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR"'
            ' parentaccount="AStd::Asset"/>'
            '</ACCOUNTS><TRANSACTIONS count="0"/></KMYMONEY-FILE>'
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert account(book, "Assets")
        assert account(book, "Assets:Cash")
        assert book.find_account("AStd--Asset") is None
        assert report.ok

    def test_a_split_price_at_odds_with_value_over_shares(
        self, store_backend, clock, tmp_path
    ):
        """Real files have prices that disagree with value/shares beyond the
        minor-unit tolerance.

        KMyMoney stores unbounded rationals for shares, not minor units, so the
        absolute tolerance `book_legs` uses for hand-entered data is too tight
        for an importer. The fix is to drop the price rather than reject the
        transaction: the booked columns are what balance.
        """
        path = tmp_path / "fx.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="2">'
            '<CURRENCY id="EUR" saf="100" name="Euro"/>'
            '<CURRENCY id="HUF" saf="100" name="Forint"/>'
            "</CURRENCIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Forint Account" type="3" currency="HUF"'
            ' parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="Euro Account" type="3" currency="EUR"'
            ' parentaccount="AStd::Asset"/>'
            "</ACCOUNTS>"
            '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01"'
            ' commodity="HUF"><SPLITS>'
            # ~810,568 HUF vs ~2171.836 EUR, with a stated price of 1/1 that is
            # plainly wrong. KMyMoney itself would show this balance happily.
            '<SPLIT account="A1" shares="-4052842/5" value="-4052842/5" price="1/1"/>'
            '<SPLIT account="A2" shares="542959/250" value="4052842/5" price="1/1"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert report.ok
        # Only the EUR leg's stated price is nonsense (1 where the implied
        # rate is ~373 HUF/EUR); the HUF-to-HUF leg is genuinely at a rate of 1.
        assert report.skipped.get("split price disagreeing with value/shares") == 1
        forint = account(book, "Assets:Forint Account")
        # Booked on the value column, with the nonsensical price quietly dropped.
        assert book.balance(forint.id).minor == -81056840
        # ...and the EUR split lost its nonsensical stored price. Its implied
        # rate stays recoverable from the booked numbers.
        transaction = book.transactions()[0]
        euro_split = next(s for s in transaction.splits if s.account_id != forint.id)
        assert euro_split.price is None
        assert float(euro_split.implied_price) > 300  # ~373 HUF per EUR

    def test_a_stock_split_is_imported_as_a_single_leg_transaction(
        self, store_backend, clock, tmp_path
    ):
        """KMyMoney writes a share split as one SPLIT with no value and a
        non-zero ``shares``. It used to be dropped as "only one split"; now
        it lands as the quantity change it is."""
        path = write(
            tmp_path / "split.xml",
            body=(
                '<SECURITIES count="1">'
                '<SECURITY id="E1" name="Acme" symbol="ACME" saf="10000" type="0"/>'
                "</SECURITIES>"
                '<ACCOUNTS count="2">'
                '<ACCOUNT id="A1" name="Cash" type="3" currency="EUR"'
                ' parentaccount="AStd::Asset"/>'
                '<ACCOUNT id="A2" name="Acme" type="15" currency="E1"'
                ' parentaccount="AStd::Asset"/>'
                "</ACCOUNTS>"
                '<TRANSACTIONS count="2">'
                # Buy 10 shares.
                '<TRANSACTION id="T1" postdate="2022-01-01" commodity="EUR"><SPLITS>'
                '<SPLIT account="A2" shares="100000/10000" value="1000/1"'
                ' price="100/1" action="Buy"/>'
                '<SPLIT account="A1" shares="-1000/1" value="-1000/1" price="1/1"/>'
                "</SPLITS></TRANSACTION>"
                # 2-for-1 split: ten more shares arrive, carrying no value.
                '<TRANSACTION id="T2" postdate="2022-11-12" commodity="EUR"><SPLITS>'
                '<SPLIT account="A2" shares="2/1" value="0/1" price="1/1"'
                ' action="Split"/>'
                "</SPLITS></TRANSACTION>"
                "</TRANSACTIONS>"
            ),
        )
        book = store_backend.open(clock=clock)
        report = import_kmymoney(path, book)
        assert report.imported["transactions"] == 2
        assert not any("only one split" in p.message for p in report.warnings)

        acme = account(book, "Assets:Acme")
        assert book.balance(acme.id).minor == 200000  # 10 -> 20 shares
        split_tx = next(t for t in book.transactions() if t.date == date(2022, 11, 12))
        assert split_tx.is_stock_split
        (leg,) = split_tx.splits
        assert leg.quantity.minor == 100000 and leg.value.minor == 0
        assert leg.action == "SPLIT"

    def test_prices_that_do_agree_are_still_kept(self, store_backend, clock, tmp_path):
        """Regression guard: the fix must not throw away a legitimate price.

        A share purchase where KMyMoney has rounded the value to cents keeps
        its stated price, because the disagreement is within tolerance.
        """
        path = tmp_path / "buy.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1">'
            '<CURRENCY id="USD" saf="100" name="Dollar"/></CURRENCIES>'
            '<SECURITIES count="1">'
            '<SECURITY id="E1" name="Thing" symbol="THG" saf="10000" type="0"/>'
            "</SECURITIES>"
            '<ACCOUNTS count="2">'
            '<ACCOUNT id="A1" name="Cash" type="3" currency="USD"'
            ' parentaccount="AStd::Asset"/>'
            '<ACCOUNT id="A2" name="Shares" type="15" currency="E1"'
            ' parentaccount="AStd::Asset"/>'
            "</ACCOUNTS>"
            '<TRANSACTIONS count="1"><TRANSACTION id="T1" postdate="2026-01-01"'
            ' commodity="USD"><SPLITS>'
            # 12 x 183.2433 = 2198.9196, stored as 2198.92 (off by 0.0004).
            '<SPLIT account="A2" shares="120000/10000" value="219892/100"'
            ' price="1832433/10000" action="Buy"/>'
            '<SPLIT account="A1" shares="-219892/100" value="-219892/100"'
            ' price="1/1"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        book = store_backend.open(clock=clock)
        import_kmymoney(path, book)
        transaction = book.transactions()[0]
        shares = transaction.split_for(account(book, "Assets:Shares").id)
        assert shares.price == Fraction(1832433, 10000)

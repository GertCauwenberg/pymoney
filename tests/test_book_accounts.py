"""Book: commodities, the account tree, and structural operations."""

from __future__ import annotations

from datetime import date

import pytest

from pymoney.core import (
    AccountClass,
    AccountType,
    Book,
    Commodity,
    CommodityKind,
    ConflictError,
    LegSpec,
    NotFoundError,
    ReconcileState,
    UpdateAccount,
    UpdateCommodity,
    ValidationError,
    new_id,
)
from pymoney.core import commands as c
from tests.conftest import Chart


class TestBookSetup:
    def test_base_currency(self, book: Book, eur: Commodity):
        assert book.base_currency.code == "EUR"
        assert book.base_currency.book_id == book.id
        # creating the book is itself one command, at r1
        assert book.revision == 1

    def test_a_book_without_a_base_currency(self, clock):
        book = Book(clock=clock)
        with pytest.raises(ValidationError, match="no base currency"):
            _ = book.base_currency

    def test_a_book_cannot_be_initialised_twice(self, clock, eur):
        book = Book(base_currency=eur, clock=clock)
        with pytest.raises(ConflictError, match="already initialised"):
            book.execute(c.InitialiseBook(name="Again", base_currency=eur))

    def test_the_bootstrap_is_one_revision(self, clock, eur):
        """A fresh replica gets the name and base currency in one batch."""
        book = Book(name="Household", base_currency=eur, clock=clock)
        page = book.pull()
        assert len(page) == 1
        batch = page.batches[0]
        assert batch.command == "InitialiseBook"
        assert {type(e).__name__ for e in batch.upserts} == {
            "BookSettings",
            "Commodity",
        }
        assert book.settings.name == "Household"

    def test_an_empty_book_still_announces_itself(self, clock):
        book = Book(name="Empty", clock=clock)
        assert book.revision == 1
        assert book.pull().batches[0].upserts[0].name == "Empty"

    def test_repr(self, book: Book):
        assert "Test Book" in repr(book)
        assert "r1" in repr(book)

    def test_set_base_currency(self, book: Book):
        usd = book.add_currency("USD")
        assert book.set_base_currency(usd.id).code == "USD"
        assert book.base_currency.code == "USD"

    def test_base_currency_must_be_a_currency(self, book: Book):
        aapl = book.add_security("AAPL")
        with pytest.raises(ValidationError, match="not a currency"):
            book.set_base_currency(aapl.id)

    def test_default_policy_targets_the_base_currency(self, book: Book):
        policy = book.default_policy()
        assert policy.target.code == "EUR"
        assert policy.as_of == date(2026, 1, 1)

    def test_default_policy_takes_a_date(self, book: Book):
        assert book.default_policy(date(2026, 6, 1)).as_of == date(2026, 6, 1)


class TestCommodities:
    def test_add_currency(self, book: Book):
        usd = book.add_currency("USD", name="US Dollar", symbol="$", scale=2)
        assert usd.minor_unit_factor == 100
        assert book.commodity(usd.id) == usd
        assert book.commodity_by_code("usd") == usd

    def test_add_security(self, book: Book):
        aapl = book.add_security("AAPL", name="Apple", isin="US0378331005")
        assert aapl.kind is CommodityKind.STOCK
        assert aapl.minor_unit_factor == 10**4

    def test_add_crypto(self, book: Book):
        btc = book.add_security("BTC", kind=CommodityKind.CRYPTO, scale=8)
        assert btc.minor_unit_factor == 10**8

    def test_codes_are_unique(self, book: Book):
        book.add_currency("USD")
        with pytest.raises(ConflictError, match="already exists"):
            book.add_currency("usd")

    def test_explicit_ids_must_be_unique(self, book: Book):
        usd = book.add_currency("USD")
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(c.CreateCommodity(code="CHF", id=usd.id))

    def test_unknown_lookups(self, book: Book):
        with pytest.raises(NotFoundError, match="no commodity"):
            book.commodity(new_id())
        with pytest.raises(NotFoundError, match="no commodity with code"):
            book.commodity_by_code("XXX")
        assert book.find_commodity("XXX") is None
        assert book.find_commodity("EUR").code == "EUR"

    def test_commodities_are_sorted(self, book: Book):
        book.add_currency("USD")
        book.add_currency("CHF")
        assert [x.code for x in book.commodities()] == ["CHF", "EUR", "USD"]

    def test_update(self, book: Book):
        usd = book.add_currency("USD")
        result = book.execute(
            UpdateCommodity(
                commodity_id=usd.id,
                name="United States Dollar",
                symbol="US$",
                display_precision=4,
                quote_source="yahoo",
                isin=None,
            )
        )
        updated = result.entity
        assert updated.name == "United States Dollar"
        assert updated.precision == 4
        assert updated.quote_source == "yahoo"
        assert updated.revision == result.revision

    def test_update_code_checks_uniqueness(self, book: Book):
        usd = book.add_currency("USD")
        book.add_currency("CHF")
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(UpdateCommodity(commodity_id=usd.id, code="CHF"))

    def test_update_code_to_itself_is_allowed(self, book: Book):
        usd = book.add_currency("USD")
        assert (
            book.execute(UpdateCommodity(commodity_id=usd.id, code="usd")).entity.code
            == "usd"
        )

    def test_a_no_op_update_burns_no_revision(self, book: Book):
        usd = book.add_currency("USD")
        before = book.revision
        result = book.execute(UpdateCommodity(commodity_id=usd.id, name=usd.name))
        assert result.revision == before
        assert result.changes == ()

    def test_delete(self, book: Book):
        usd = book.add_currency("USD")
        book.execute(c.DeleteCommodity(commodity_id=usd.id))
        with pytest.raises(NotFoundError):
            book.commodity(usd.id)

    def test_cannot_delete_the_base_currency(self, book: Book):
        with pytest.raises(ValidationError, match="base currency"):
            book.execute(c.DeleteCommodity(commodity_id=book.base_currency.id))

    def test_cannot_delete_a_commodity_in_use_by_an_account(self, book: Book):
        usd = book.add_currency("USD")
        book.add_account("Wallet", AccountType.CASH, commodity=usd)
        with pytest.raises(ConflictError, match="used by 1 account"):
            book.execute(c.DeleteCommodity(commodity_id=usd.id))

    def test_cannot_delete_a_commodity_with_prices(self, book: Book):
        usd = book.add_currency("USD")
        book.add_price(usd, book.base_currency, "0.9", effective_date=date(2026, 1, 1))
        with pytest.raises(ConflictError, match="price"):
            book.execute(c.DeleteCommodity(commodity_id=usd.id))


class TestAccountCreation:
    def test_top_level(self, book: Book):
        account = book.add_account("Assets", AccountType.ASSET)
        assert account.parent_id is None
        assert account.commodity.id == book.base_currency.id
        assert book.path(account) == "Assets"
        assert book.roots() == (account,)

    def test_nested(self, book: Book):
        assets = book.add_account("Assets", AccountType.ASSET, placeholder=True)
        checking = book.add_account("Checking", AccountType.CHECKING, parent=assets)
        assert book.path(checking) == "Assets:Checking"
        assert book.children(assets.id) == (checking,)
        assert book.parent(checking).id == assets.id
        assert book.parent(assets) is None

    def test_deeply_nested_paths(self, chart: Chart):
        book = chart.book
        assert book.path(chart.aapl_account) == "Assets:Brokerage:AAPL"
        assert [a.name for a in book.ancestors(chart.aapl_account)] == [
            "Brokerage",
            "Assets",
        ]

    def test_accepts_an_id_instead_of_an_entity(self, book: Book):
        assets = book.add_account("Assets", AccountType.ASSET)
        child = book.add_account("Cash", AccountType.CASH, parent=assets.id)
        assert child.parent_id == assets.id

    def test_foreign_currency_account(self, book: Book):
        usd = book.add_currency("USD")
        account = book.add_account("Wallet", AccountType.CASH, commodity=usd)
        assert account.commodity.code == "USD"

    def test_all_metadata_is_kept(self, book: Book):
        account = book.add_account(
            "Checking",
            AccountType.CHECKING,
            description="main",
            code="1000",
            notes="n",
            institution="Bank",
            iban="DE00",
        )
        assert account.description == "main"
        assert account.code == "1000"
        assert account.institution == "Bank"
        assert account.iban == "DE00"

    def test_sibling_names_must_be_unique(self, book: Book):
        book.add_account("Assets", AccountType.ASSET)
        with pytest.raises(ConflictError, match="top level already has"):
            book.add_account("assets", AccountType.ASSET)

    def test_same_name_under_different_parents_is_fine(self, chart: Chart):
        book = chart.book
        a = book.add_account("Misc", AccountType.EXPENSE, parent=chart.expenses)
        b = book.add_account("Misc", AccountType.ASSET, parent=chart.assets)
        assert book.path(a) == "Expenses:Misc"
        assert book.path(b) == "Assets:Misc"

    def test_duplicate_child_names_are_refused(self, chart: Chart):
        with pytest.raises(ConflictError, match=r"Expenses.*already has"):
            chart.book.add_account("Fuel", AccountType.EXPENSE, parent=chart.expenses)

    def test_children_must_match_the_parent_class(self, chart: Chart):
        with pytest.raises(ValidationError, match="cannot put a"):
            chart.book.add_account("Wrong", AccountType.EXPENSE, parent=chart.assets)

    def test_subtypes_may_differ_within_a_class(self, chart: Chart):
        """A SAVINGS account under an ASSET placeholder is fine."""
        account = chart.book.add_account(
            "Rainy Day", AccountType.SAVINGS, parent=chart.assets
        )
        assert account.account_class is AccountClass.ASSET

    def test_unknown_parent(self, book: Book):
        with pytest.raises(NotFoundError, match="no account"):
            book.add_account("X", AccountType.ASSET, parent=new_id())

    def test_unknown_commodity(self, book: Book):
        with pytest.raises(NotFoundError, match="no commodity"):
            book.add_account("X", AccountType.ASSET, commodity=new_id())

    def test_explicit_id_must_be_unique(self, book: Book):
        account = book.add_account("Assets", AccountType.ASSET)
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(
                c.CreateAccount(name="Other", type=AccountType.ASSET, id=account.id)
            )


class TestAccountQueries:
    def test_accounts_filtered_by_class(self, chart: Chart):
        expenses = chart.book.accounts(account_class=AccountClass.EXPENSE)
        assert {a.name for a in expenses} == {
            "Expenses",
            "Groceries",
            "Fuel",
            "Bank Fees",
        }

    def test_accounts_filtered_by_type(self, chart: Chart):
        stocks = chart.book.accounts(type=AccountType.STOCK)
        assert [a.name for a in stocks] == ["AAPL"]

    def test_accounts_are_sorted_by_path(self, chart: Chart):
        paths = [chart.book.path(a) for a in chart.book.accounts()]
        assert paths == sorted(paths)

    def test_subtree_and_descendants(self, chart: Chart):
        book = chart.book
        subtree = {a.name for a in book.subtree(chart.assets)}
        assert subtree == {
            "Assets",
            "Checking",
            "Savings",
            "USD Wallet",
            "Brokerage",
            "AAPL",
        }
        assert chart.assets.id not in {a.id for a in book.descendants(chart.assets)}

    def test_account_by_path(self, chart: Chart):
        book = chart.book
        assert book.account_by_path("Assets:Brokerage:AAPL").id == chart.aapl_account.id
        assert book.account_by_path("assets:brokerage:aapl").id == chart.aapl_account.id
        assert book.account_by_path(":Assets:").id == chart.assets.id

    def test_account_by_path_failures(self, chart: Chart):
        book = chart.book
        with pytest.raises(NotFoundError, match="empty account path"):
            book.account_by_path("")
        with pytest.raises(NotFoundError, match="no account at path"):
            book.account_by_path("Assets:Nope")
        assert book.find_account("Assets:Nope") is None
        assert book.find_account("Assets").id == chart.assets.id

    def test_children_of_a_leaf_is_empty(self, chart: Chart):
        assert chart.book.children(chart.fuel.id) == ()

    def test_include_closed_filter(self, chart: Chart):
        book = chart.book
        book.execute(c.SetAccountClosed(account_id=chart.savings.id))
        names = {a.name for a in book.accounts(include_closed=False)}
        assert "Savings" not in names
        assert "Savings" in {a.name for a in book.accounts()}


class TestRenameAndMove:
    def test_rename(self, chart: Chart):
        book = chart.book
        result = book.execute(c.RenameAccount(account_id=chart.fuel.id, name="Car Fuel"))
        assert book.path(result.entity) == "Expenses:Car Fuel"

    def test_rename_does_not_touch_transactions(self, funded: Chart):
        """Paths are derived, so a rename is one row (decision #13)."""
        book = funded.book
        before = book.balance(funded.groceries.id)
        result = book.execute(
            c.RenameAccount(account_id=funded.groceries.id, name="Food")
        )
        assert len(result.changes) == 1
        assert book.balance(funded.groceries.id) == before
        assert book.path(funded.groceries.id) == "Expenses:Food"

    def test_rename_records_the_old_name(self, chart: Chart):
        result = chart.book.execute(
            c.RenameAccount(account_id=chart.fuel.id, name="Petrol")
        )
        assert "renamed from 'Fuel'" in result.changes[0].note

    def test_renaming_to_the_same_name_is_a_no_op(self, chart: Chart):
        before = chart.book.revision
        result = chart.book.execute(
            c.RenameAccount(account_id=chart.fuel.id, name="Fuel")
        )
        assert result.revision == before

    def test_rename_collision(self, chart: Chart):
        with pytest.raises(ConflictError, match="already has an account"):
            chart.book.execute(
                c.RenameAccount(account_id=chart.fuel.id, name="Groceries")
            )

    def test_rename_rejects_a_separator(self, chart: Chart):
        with pytest.raises(ValidationError, match="must not contain"):
            chart.book.execute(c.RenameAccount(account_id=chart.fuel.id, name="Car:Fuel"))

    def test_move(self, chart: Chart):
        book = chart.book
        car = book.add_account("Car", AccountType.EXPENSE, parent=chart.expenses)
        book.execute(c.MoveAccount(account_id=chart.fuel.id, new_parent_id=car.id))
        assert book.path(chart.fuel.id) == "Expenses:Car:Fuel"

    def test_move_to_the_top_level(self, chart: Chart):
        book = chart.book
        book.execute(c.MoveAccount(account_id=chart.fuel.id, new_parent_id=None))
        assert book.path(chart.fuel.id) == "Fuel"
        # entities are immutable snapshots, so compare by id, not by value
        assert chart.fuel.id in {a.id for a in book.roots()}

    def test_move_keeps_history(self, funded: Chart):
        book = funded.book
        before = book.balance(funded.fuel.id)
        car = book.add_account("Car", AccountType.EXPENSE, parent=funded.expenses)
        book.execute(c.MoveAccount(account_id=funded.fuel.id, new_parent_id=car.id))
        assert book.balance(funded.fuel.id) == before

    def test_move_records_the_old_path(self, chart: Chart):
        book = chart.book
        car = book.add_account("Car", AccountType.EXPENSE, parent=chart.expenses)
        result = book.execute(
            c.MoveAccount(account_id=chart.fuel.id, new_parent_id=car.id)
        )
        assert "moved from 'Expenses:Fuel'" in result.changes[0].note

    def test_move_to_the_same_parent_is_a_no_op(self, chart: Chart):
        before = chart.book.revision
        result = chart.book.execute(
            c.MoveAccount(account_id=chart.fuel.id, new_parent_id=chart.expenses.id)
        )
        assert result.revision == before

    def test_cannot_be_its_own_parent(self, chart: Chart):
        with pytest.raises(ValidationError, match="its own parent"):
            chart.book.execute(
                c.MoveAccount(account_id=chart.fuel.id, new_parent_id=chart.fuel.id)
            )

    def test_cannot_move_under_a_descendant(self, chart: Chart):
        """The classic tree-corruption bug, refused up front."""
        with pytest.raises(ValidationError, match=r"own.*descendant"):
            chart.book.execute(
                c.MoveAccount(
                    account_id=chart.assets.id,
                    new_parent_id=chart.aapl_account.id,
                )
            )

    def test_cannot_move_across_classes(self, chart: Chart):
        with pytest.raises(ValidationError, match="cannot move a"):
            chart.book.execute(
                c.MoveAccount(account_id=chart.fuel.id, new_parent_id=chart.assets.id)
            )

    def test_move_collision(self, chart: Chart):
        book = chart.book
        other = book.add_account("Other", AccountType.EXPENSE, parent=chart.expenses)
        book.add_account("Fuel", AccountType.EXPENSE, parent=other)
        with pytest.raises(ConflictError, match="already has an account"):
            book.execute(c.MoveAccount(account_id=chart.fuel.id, new_parent_id=other.id))


class TestUpdateAndClose:
    def test_update_metadata(self, chart: Chart):
        result = chart.book.execute(
            UpdateAccount(
                account_id=chart.checking.id,
                description="Everyday",
                code="1001",
                notes="note",
                institution="Bank",
                iban="DE12",
            )
        )
        assert result.entity.description == "Everyday"
        assert result.entity.iban == "DE12"

    def test_no_op_update(self, chart: Chart):
        before = chart.book.revision
        result = chart.book.execute(
            UpdateAccount(account_id=chart.checking.id, description="")
        )
        assert result.revision == before

    def test_becoming_a_placeholder_requires_no_postings(self, funded: Chart):
        with pytest.raises(ValidationError, match="has postings"):
            funded.book.execute(
                UpdateAccount(account_id=funded.checking.id, placeholder=True)
            )

    def test_an_unused_account_may_become_a_placeholder(self, chart: Chart):
        result = chart.book.execute(
            UpdateAccount(account_id=chart.savings.id, placeholder=True)
        )
        assert result.entity.placeholder

    def test_close_and_reopen(self, chart: Chart):
        book = chart.book
        closed = book.execute(c.SetAccountClosed(account_id=chart.savings.id)).entity
        assert closed.closed
        reopened = book.execute(
            c.SetAccountClosed(account_id=chart.savings.id, closed=False)
        ).entity
        assert not reopened.closed

    def test_closing_twice_is_a_no_op(self, chart: Chart):
        book = chart.book
        book.execute(c.SetAccountClosed(account_id=chart.savings.id))
        before = book.revision
        result = book.execute(c.SetAccountClosed(account_id=chart.savings.id))
        assert result.revision == before

    def test_cannot_close_while_children_are_open(self, chart: Chart):
        with pytest.raises(ValidationError, match="open sub-account"):
            chart.book.execute(c.SetAccountClosed(account_id=chart.brokerage.id))

    def test_can_close_once_children_are_closed(self, chart: Chart):
        book = chart.book
        book.execute(c.SetAccountClosed(account_id=chart.aapl_account.id))
        assert book.execute(
            c.SetAccountClosed(account_id=chart.brokerage.id)
        ).entity.closed

    def test_closed_accounts_reject_new_postings(self, chart: Chart):
        book = chart.book
        book.execute(c.SetAccountClosed(account_id=chart.savings.id))
        with pytest.raises(ValidationError, match="is closed"):
            book.post(
                date(2026, 2, 1),
                [LegSpec.of(chart.savings.id, "10"), LegSpec.of(chart.salary.id)],
            )

    def test_placeholders_reject_postings(self, chart: Chart):
        with pytest.raises(ValidationError, match="placeholder"):
            chart.book.post(
                date(2026, 2, 1),
                [LegSpec.of(chart.assets.id, "10"), LegSpec.of(chart.salary.id)],
            )


class TestDeleteAccount:
    def test_delete_an_unused_leaf(self, chart: Chart):
        book = chart.book
        book.execute(c.DeleteAccount(account_id=chart.savings.id))
        with pytest.raises(NotFoundError):
            book.account(chart.savings.id)
        assert chart.savings.id not in {a.id for a in book.children(chart.assets.id)}

    def test_cannot_delete_with_children(self, chart: Chart):
        with pytest.raises(ConflictError, match="has sub-accounts"):
            chart.book.execute(c.DeleteAccount(account_id=chart.assets.id))

    def test_cannot_delete_with_postings(self, funded: Chart):
        with pytest.raises(ConflictError, match=r"used by .* transaction"):
            funded.book.execute(c.DeleteAccount(account_id=funded.checking.id))

    def test_the_error_suggests_merging(self, funded: Chart):
        with pytest.raises(ConflictError, match="merge it into another account"):
            funded.book.execute(c.DeleteAccount(account_id=funded.checking.id))


class TestMergeAccounts:
    def test_splits_are_repointed(self, funded: Chart):
        book = funded.book
        target = book.add_account("Food", AccountType.EXPENSE, parent=funded.expenses)
        before = book.balance(funded.groceries.id)
        book.execute(c.MergeAccounts(source_id=funded.groceries.id, target_id=target.id))
        assert book.balance(target.id) == before
        with pytest.raises(NotFoundError):
            book.account(funded.groceries.id)

    def test_balances_add_up(self, funded: Chart):
        book = funded.book
        book.post(
            date(2026, 1, 20),
            [LegSpec.of(funded.checking.id, "-10.00"), LegSpec.of(funded.fuel.id)],
        )
        total = book.balance(funded.groceries.id) + book.balance(funded.fuel.id)
        book.execute(
            c.MergeAccounts(source_id=funded.fuel.id, target_id=funded.groceries.id)
        )
        assert book.balance(funded.groceries.id) == total

    def test_children_are_moved(self, chart: Chart):
        book = chart.book
        sub = book.add_account("Diesel", AccountType.EXPENSE, parent=chart.fuel)
        book.execute(
            c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.groceries.id)
        )
        assert book.path(sub.id) == "Expenses:Groceries:Diesel"

    def test_one_revision_for_the_whole_merge(self, funded: Chart):
        """A merge is one sync step, not 400 (decision #13)."""
        book = funded.book
        before = book.revision
        result = book.execute(
            c.MergeAccounts(source_id=funded.fuel.id, target_id=funded.groceries.id)
        )
        assert result.revision == before + 1
        assert len({ch.revision for ch in result.changes}) == 1

    def test_merge_is_recorded_for_every_touched_row(self, funded: Chart):
        book = funded.book
        result = book.execute(
            c.MergeAccounts(source_id=funded.fuel.id, target_id=funded.groceries.id)
        )
        kinds = [ch.kind.value for ch in result.changes]
        assert "TRANSACTION" in kinds
        assert kinds[-1] == "ACCOUNT"
        assert result.changes[-1].op.value == "DELETE"

    def test_cannot_merge_into_itself(self, chart: Chart):
        with pytest.raises(ValidationError, match="into itself"):
            chart.book.execute(
                c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.fuel.id)
            )

    def test_cannot_merge_across_commodities(self, chart: Chart):
        """Quantities would become meaningless."""
        with pytest.raises(ValidationError, match="cannot merge"):
            chart.book.execute(
                c.MergeAccounts(
                    source_id=chart.usd_account.id,
                    target_id=chart.checking.id,
                )
            )

    def test_cannot_merge_across_classes(self, chart: Chart):
        book = chart.book
        usd_expense = book.add_account(
            "Foreign Fees",
            AccountType.EXPENSE,
            parent=chart.expenses,
            commodity=chart.usd,
        )
        with pytest.raises(ValidationError, match="cannot merge a"):
            book.execute(
                c.MergeAccounts(source_id=usd_expense.id, target_id=chart.usd_account.id)
            )

    def test_cannot_merge_into_a_descendant(self, chart: Chart):
        book = chart.book
        sub = book.add_account("Diesel", AccountType.EXPENSE, parent=chart.fuel)
        with pytest.raises(ValidationError, match="own descendant"):
            book.execute(c.MergeAccounts(source_id=chart.fuel.id, target_id=sub.id))

    def test_name_collision_among_moved_children(self, chart: Chart):
        book = chart.book
        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.fuel)
        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.groceries)
        with pytest.raises(ConflictError, match="already has an account"):
            book.execute(
                c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.groceries.id)
            )

    def test_a_failed_merge_changes_nothing(self, chart: Chart):
        """Atomicity: the partially-moved child must be rolled back."""
        book = chart.book
        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.fuel)
        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.groceries)
        before_revision = book.revision
        before_paths = {book.path(a) for a in book.accounts()}
        with pytest.raises(ConflictError):
            book.execute(
                c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.groceries.id)
            )
        assert book.revision == before_revision
        assert {book.path(a) for a in book.accounts()} == before_paths
        assert book.check_integrity() == ()

    def test_merging_reconciled_history_is_allowed(self, funded: Chart):
        """A merge does not change any value, so the lock does not apply."""
        book = funded.book
        transaction = book.transactions(account_id=funded.groceries.id)[0]
        split = transaction.split_for(funded.groceries.id)
        book.execute(
            c.SetSplitReconcileState(
                transaction_id=transaction.id,
                split_id=split.id,
                state=ReconcileState.RECONCILED,
            )
        )
        target = book.add_account("Food", AccountType.EXPENSE, parent=funded.expenses)
        book.execute(c.MergeAccounts(source_id=funded.groceries.id, target_id=target.id))
        assert book.balance(target.id).minor == 8420


class TestBookSettings:
    """The book's own fields travel through the command path too."""

    def test_settings_snapshot(self, book: Book):
        settings = book.settings
        assert settings.id == book.id
        assert settings.name == "Test Book"
        assert settings.base_currency_id == book.base_currency.id
        assert settings.revision == 1
        assert "Test Book" in str(settings)

    def test_set_base_currency_is_a_command(self, book: Book):
        """It used to mutate state silently; a replica would never have known."""
        usd = book.add_currency("USD")
        before = book.revision
        result = book.execute(c.SetBaseCurrency(commodity_id=usd.id))
        assert result.revision == before + 1
        assert book.base_currency.code == "USD"
        assert book.settings.revision == result.revision
        assert len(result.changes) == 1
        assert result.changes[0].kind.value == "BOOK"
        assert result.changes[0].entity_id == book.id
        assert "EUR -> USD" in result.changes[0].note

    def test_the_facade_still_works(self, book: Book):
        usd = book.add_currency("USD")
        assert book.set_base_currency(usd.id).code == "USD"
        assert book.changelog.head == book.revision

    def test_setting_the_same_currency_is_a_no_op(self, book: Book):
        before = book.revision
        result = book.execute(c.SetBaseCurrency(commodity_id=book.base_currency.id))
        assert result.revision == before
        assert result.changes == ()

    def test_the_base_currency_must_be_a_currency(self, book: Book):
        aapl = book.add_security("AAPL")
        with pytest.raises(ValidationError, match="not a currency"):
            book.execute(c.SetBaseCurrency(commodity_id=aapl.id))

    def test_changing_it_does_not_touch_stored_amounts(self, funded: Chart):
        """Safe by construction: amounts are per-commodity, not per-book."""
        book = funded.book
        before = book.balance(funded.checking.id)
        usd = book.commodity_by_code("USD")
        book.execute(c.SetBaseCurrency(commodity_id=usd.id))
        assert book.balance(funded.checking.id) == before
        assert book.balance(funded.checking.id).commodity.code == "EUR"
        assert book.check_integrity() == ()

    def test_it_changes_the_default_for_new_accounts(self, book: Book):
        usd = book.add_currency("USD")
        book.execute(c.SetBaseCurrency(commodity_id=usd.id))
        account = book.add_account("Wallet", AccountType.CASH)
        assert account.commodity.code == "USD"

    def test_rename_book(self, book: Book):
        result = book.execute(c.RenameBook(name="Household"))
        assert book.name == "Household"
        assert book.settings.name == "Household"
        assert "renamed from 'Test Book'" in result.changes[0].note

    def test_rename_to_the_same_name_is_a_no_op(self, book: Book):
        before = book.revision
        assert book.execute(c.RenameBook(name="Test Book")).revision == before

    def test_an_empty_book_name_is_refused(self, book: Book):
        with pytest.raises(ValidationError, match="must not be empty"):
            book.execute(c.RenameBook(name="   "))

    def test_a_failed_settings_change_rolls_back(self, book: Book):
        from pymoney.core import Command
        from pymoney.core.handlers import HANDLERS

        class Sabotage(Command):
            pass

        def handler(bk, command):
            bk.set_book_field("name", "Broken")
            raise RuntimeError("boom")

        HANDLERS[Sabotage] = handler
        try:
            before = book.revision
            with pytest.raises(RuntimeError):
                book.execute(Sabotage())
            assert book.name == "Test Book"
            assert book.settings.revision == 1
            assert book.revision == before
        finally:
            del HANDLERS[Sabotage]


class TestAccountCommodity:
    """Changing an account's commodity: narrowly allowed, firmly guarded."""

    def test_an_unused_account_can_be_corrected(self, chart: Chart):
        """The 'I picked the wrong currency' case, which is real and harmless."""
        book = chart.book
        result = book.execute(
            c.SetAccountCommodity(account_id=chart.savings.id, commodity_id=chart.usd.id)
        )
        assert result.entity.commodity.code == "USD"
        assert "EUR -> USD" in result.changes[0].note
        assert book.balance(chart.savings.id).commodity.code == "USD"

    def test_an_account_with_postings_refuses(self, funded: Chart):
        """Otherwise 84.20 EUR of groceries would silently become 84.20 USD."""
        book = funded.book
        with pytest.raises(ConflictError, match="would silently reinterpret"):
            book.execute(
                c.SetAccountCommodity(
                    account_id=funded.checking.id, commodity_id=funded.usd.id
                )
            )

    def test_the_error_suggests_the_safe_alternative(self, funded: Chart):
        book = funded.book
        with pytest.raises(ConflictError, match="transfer the balance instead"):
            book.execute(
                c.SetAccountCommodity(
                    account_id=funded.checking.id, commodity_id=funded.usd.id
                )
            )

    def test_setting_the_same_commodity_is_a_no_op(self, funded: Chart):
        """Even on an account with history, since nothing changes."""
        book = funded.book
        before = book.revision
        result = book.execute(
            c.SetAccountCommodity(
                account_id=funded.checking.id, commodity_id=funded.eur.id
            )
        )
        assert result.revision == before

    def test_an_unknown_commodity_is_refused(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no commodity"):
            chart.book.execute(
                c.SetAccountCommodity(account_id=chart.savings.id, commodity_id=new_id())
            )

    def test_deleting_all_postings_re_enables_it(self, funded: Chart):
        book = funded.book
        for transaction in book.transactions(account_id=funded.card.id):
            book.execute(c.DeleteTransaction(transaction_id=transaction.id))
        result = book.execute(
            c.SetAccountCommodity(account_id=funded.card.id, commodity_id=funded.usd.id)
        )
        assert result.entity.commodity.code == "USD"


class TestCommodityFactorIsImmutable:
    """Decision #3's safety net: minor units must never be reinterpreted."""

    def test_no_command_can_change_the_minor_unit_factor(self):
        fields = set(UpdateCommodity.__dataclass_fields__)
        assert "minor_unit_factor" not in fields
        assert "display_precision" in fields  # display only, safe to change

    def test_no_command_can_change_an_account_commodity_in_place(self):
        assert "commodity_id" not in set(UpdateAccount.__dataclass_fields__)

    def test_display_precision_does_not_affect_stored_amounts(self, funded: Chart):
        book = funded.book
        before = book.balance(funded.checking.id)
        book.execute(UpdateCommodity(commodity_id=funded.eur.id, display_precision=4))
        after = book.balance(funded.checking.id)
        assert after.minor == before.minor
        assert after == before  # equality is by commodity id, not by snapshot


class TestLiveCommoditySnapshots:
    """Entities embed a Commodity; the book must still hand out current ones."""

    def test_balances_follow_a_commodity_edit(self, funded: Chart):
        book = funded.book
        assert book.balance(funded.checking.id).format() == "13115.80 \u20ac"
        book.execute(
            UpdateCommodity(commodity_id=funded.eur.id, symbol="EUR", display_precision=4)
        )
        assert book.balance(funded.checking.id).format() == "13115.8000 EUR"

    def test_registers_follow_a_commodity_edit(self, funded: Chart):
        book = funded.book
        book.execute(
            UpdateCommodity(commodity_id=funded.eur.id, symbol="EUR", display_precision=3)
        )
        ref = book.register(funded.checking.id)[0]
        assert ref.quantity.format() == "10000.000 EUR"
        assert ref.value.format() == "10000.000 EUR"
        # the stored row keeps its historical snapshot untouched
        assert ref.split.quantity.commodity.precision == 2

    def test_balances_by_account_follows_too(self, funded: Chart):
        book = funded.book
        book.execute(UpdateCommodity(commodity_id=funded.eur.id, display_precision=3))
        balances = book.balances_by_account()
        assert balances[funded.checking.id].commodity.precision == 3

    def test_live_resolves_a_stale_snapshot(self, funded: Chart):
        book = funded.book
        stale = book.account(funded.checking.id).commodity
        book.execute(UpdateCommodity(commodity_id=funded.eur.id, display_precision=4))
        assert stale.precision == 2
        assert book.live(stale).precision == 4

    def test_live_passes_through_an_unknown_commodity(self, funded: Chart):
        """A detached commodity is returned as-is rather than blowing up."""
        detached = Commodity.currency("ZZZ")
        assert funded.book.live(detached) is detached

    def test_arithmetic_is_never_affected(self, funded: Chart):
        """minor_unit_factor is immutable, so sums cannot be reinterpreted."""
        book = funded.book
        before = book.balance(funded.checking.id).minor
        book.execute(UpdateCommodity(commodity_id=funded.eur.id, display_precision=6))
        assert book.balance(funded.checking.id).minor == before
        assert book.check_integrity() == ()

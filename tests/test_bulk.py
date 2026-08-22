"""Bulk mode: many commands, one unit of work.

The trade is explicit and has to be tested from both sides: inside a
``book.bulk()`` block the per-command atomicity is gone, so a single failure
discards the whole batch. That is right for an importer and wrong for a UI, and
either way it must not be a surprise.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pymoney.core import (
    AccountType,
    ConflictError,
    LegSpec,
    UnbalancedTransactionError,
)
from pymoney.core import (
    commands as c,
)
from tests.conftest import Chart


class TestBulkSucceeding:
    def test_everything_lands(self, chart: Chart):
        book = chart.book
        with book.bulk():
            for i in range(25):
                book.post(
                    date(2026, 1, 1) + timedelta(days=i),
                    [
                        LegSpec.of(chart.checking.id, f"-{i + 1}.00"),
                        LegSpec.of(chart.groceries.id),
                    ],
                )
        assert len(book.transactions()) == 25
        assert book.balance(chart.groceries.id).minor == sum(
            (i + 1) * 100 for i in range(25)
        )
        assert book.check_integrity() == ()

    def test_revisions_are_still_one_per_command(self, chart: Chart):
        """Batching the commit does not batch the revisions: a replica still
        sees each command as its own atomically applicable step."""
        book = chart.book
        before = book.revision
        with book.bulk():
            for i in range(5):
                book.post(
                    date(2026, 1, 1) + timedelta(days=i),
                    [
                        LegSpec.of(chart.checking.id, "-1.00"),
                        LegSpec.of(chart.groceries.id),
                    ],
                )
        assert book.revision == before + 5
        assert len({c.revision for c in book.changes_since(before)}) == 5
        page = book.pull(before)
        assert len(page) == 5
        assert page.complete

    def test_the_watermark_is_persisted_at_the_end(self, chart: Chart):
        book = chart.book
        with book.bulk():
            book.post(
                date(2026, 1, 1),
                [LegSpec.of(chart.checking.id, "-1.00"), LegSpec.of(chart.fuel.id)],
            )
        assert book.store.load_book().revision == book.revision

    def test_results_are_returned_as_usual(self, chart: Chart):
        book = chart.book
        with book.bulk():
            transaction = book.post(
                date(2026, 1, 1),
                [LegSpec.of(chart.checking.id, "-5.00"), LegSpec.of(chart.fuel.id)],
            )
        assert book.transaction(transaction.id).memo == transaction.memo

    def test_reads_inside_the_block_see_the_writes(self, chart: Chart):
        """An importer needs to look things up as it goes."""
        book = chart.book
        with book.bulk():
            payee = book.add_payee("Shop")
            assert book.find_payee("shop").id == payee.id
            book.post(
                date(2026, 1, 1),
                [LegSpec.of(chart.checking.id, "-5.00"), LegSpec.of(chart.fuel.id)],
                payee_id=payee.id,
            )
            assert book.balance(chart.fuel.id).minor == 500

    def test_nesting_is_re_entrant(self, chart: Chart):
        """A helper that batches, called from a caller that also batches, must
        not commit the outer block early."""
        book = chart.book
        with book.bulk():
            book.post(
                date(2026, 1, 1),
                [LegSpec.of(chart.checking.id, "-1.00"), LegSpec.of(chart.fuel.id)],
            )
            with book.bulk():
                book.post(
                    date(2026, 1, 2),
                    [LegSpec.of(chart.checking.id, "-2.00"), LegSpec.of(chart.fuel.id)],
                )
            assert len(book.transactions()) == 2
        assert len(book.transactions()) == 2
        assert book.balance(chart.fuel.id).minor == 300

    def test_an_empty_block_is_harmless(self, chart: Chart):
        book = chart.book
        before = book.revision
        with book.bulk():
            pass
        assert book.revision == before


class TestBulkFailing:
    def test_one_failure_discards_the_whole_batch(self, chart: Chart):
        """The trade, stated as a test. Half an imported statement is not worth
        keeping, so nothing is."""
        book = chart.book
        before = book.revision
        with pytest.raises(UnbalancedTransactionError), book.bulk():
            for i in range(10):
                book.post(
                    date(2026, 1, 1) + timedelta(days=i),
                    [
                        LegSpec.of(chart.checking.id, "-1.00"),
                        LegSpec.of(chart.groceries.id),
                    ],
                )
            book.post(
                date(2026, 2, 1),
                [
                    LegSpec.of(chart.checking.id, "-10.00"),
                    LegSpec.of(chart.groceries.id, "9.00"),
                ],
            )
        assert book.transactions() == ()
        assert book.revision == before
        assert book.balance(chart.groceries.id).is_zero
        assert book.changes_since(before) == ()
        assert book.check_integrity() == ()

    def test_the_revision_watermark_is_restored(self, chart: Chart):
        book = chart.book
        before = book.revision
        with pytest.raises(ConflictError), book.bulk():
            book.add_account("Holiday", AccountType.SAVINGS, parent=chart.assets)
            book.add_account("holiday", AccountType.SAVINGS, parent=chart.assets)
        assert book.revision == before
        assert book.store.load_book().revision == before
        assert book.find_account("Assets:Holiday") is None

    def test_book_settings_are_restored(self, chart: Chart):
        book = chart.book
        name = book.name
        with pytest.raises(ConflictError), book.bulk():
            book.execute(c.RenameBook(name="Renamed"))
            book.add_currency("EUR")  # already exists
        assert book.name == name
        assert book.settings.name == name

    def test_the_book_works_again_afterwards(self, chart: Chart):
        """A failed batch must leave the book usable, not wedged."""
        book = chart.book
        with pytest.raises(UnbalancedTransactionError), book.bulk():
            book.post(
                date(2026, 1, 1),
                [
                    LegSpec.of(chart.checking.id, "-10.00"),
                    LegSpec.of(chart.groceries.id, "9.00"),
                ],
            )
        transaction = book.post(
            date(2026, 1, 2),
            [LegSpec.of(chart.checking.id, "-3.00"), LegSpec.of(chart.fuel.id)],
        )
        assert book.transaction(transaction.id).date == date(2026, 1, 2)
        assert book.balance(chart.fuel.id).minor == 300

    def test_a_failure_in_a_nested_block_fails_the_outer_one(self, chart: Chart):
        book = chart.book
        before = book.revision
        with pytest.raises(UnbalancedTransactionError), book.bulk():
            book.post(
                date(2026, 1, 1),
                [LegSpec.of(chart.checking.id, "-1.00"), LegSpec.of(chart.fuel.id)],
            )
            with book.bulk():
                book.post(
                    date(2026, 1, 2),
                    [
                        LegSpec.of(chart.checking.id, "-10.00"),
                        LegSpec.of(chart.groceries.id, "9.00"),
                    ],
                )
        assert book.revision == before
        assert book.transactions() == ()


class TestBulkIsolation:
    """A failure the caller *catches* must undo only its own command.

    This is what an importer needs: one malformed row out of ten years should be
    reported and skipped, not cost the other ten years. It is also where SQLite
    needed help -- pysqlite does not emit BEGIN before a SAVEPOINT, so without
    the engine taking transaction control the savepoint silently does nothing and
    a rolled-back command stays written.
    """

    def test_a_caught_failure_leaves_the_rest_of_the_batch(self, chart: Chart):
        book = chart.book
        before = book.revision
        with book.bulk():
            for i in range(3):
                book.post(
                    date(2026, 1, 1) + timedelta(days=i),
                    [
                        LegSpec.of(chart.checking.id, f"-{i + 1}.00"),
                        LegSpec.of(chart.groceries.id),
                    ],
                )
            with pytest.raises(UnbalancedTransactionError):
                book.post(
                    date(2026, 2, 1),
                    [
                        LegSpec.of(chart.checking.id, "-10.00"),
                        LegSpec.of(chart.groceries.id, "9.00"),
                    ],
                )
            book.post(
                date(2026, 3, 1),
                [LegSpec.of(chart.checking.id, "-4.00"), LegSpec.of(chart.fuel.id)],
            )
        assert len(book.transactions()) == 4
        assert book.balance(chart.groceries.id).minor == 600
        assert book.balance(chart.fuel.id).minor == 400
        assert book.revision == before + 4
        assert book.check_integrity() == ()

    def test_the_failed_command_left_nothing(self, chart: Chart):
        book = chart.book
        with book.bulk():
            with pytest.raises(UnbalancedTransactionError):
                book.post(
                    date(2026, 2, 1),
                    [
                        LegSpec.of(chart.checking.id, "-10.00"),
                        LegSpec.of(chart.groceries.id, "9.00"),
                    ],
                )
            book.post(
                date(2026, 3, 1),
                [LegSpec.of(chart.checking.id, "-4.00"), LegSpec.of(chart.fuel.id)],
            )
        assert [t.date for t in book.transactions()] == [date(2026, 3, 1)]
        assert book.balance(chart.groceries.id).is_zero

    def test_a_failed_command_does_not_consume_a_revision(self, chart: Chart):
        book = chart.book
        before = book.revision
        with book.bulk():
            with pytest.raises(ConflictError):
                book.add_account("Groceries", AccountType.EXPENSE, parent=chart.expenses)
            book.post(
                date(2026, 3, 1),
                [LegSpec.of(chart.checking.id, "-4.00"), LegSpec.of(chart.fuel.id)],
            )
        assert book.revision == before + 1
        assert len(book.pull(before)) == 1

    def test_partial_writes_of_a_failed_command_are_undone(self, chart: Chart):
        """A merge touches many rows before it fails; none may survive."""
        book = chart.book
        from pymoney.core import commands as c

        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.fuel)
        book.add_account("Diesel", AccountType.EXPENSE, parent=chart.groceries)
        paths = {book.path(a) for a in book.accounts()}
        with book.bulk():
            with pytest.raises(ConflictError):
                book.execute(
                    c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.groceries.id)
                )
            book.post(
                date(2026, 3, 1),
                [LegSpec.of(chart.checking.id, "-4.00"), LegSpec.of(chart.fuel.id)],
            )
        assert {book.path(a) for a in book.accounts()} == paths
        assert book.check_integrity() == ()

"""The Store contract, asserted against every backend.

Separate from ``test_store.py`` on purpose. That module defines its own
SQL-specific ``book`` fixture, which shadows the ``--stores`` parametrised one
from conftest -- so these tests, which are about the contract *every* store
shares, would quietly have run on SQL only.
"""

from __future__ import annotations

from datetime import date

import pytest

from pymoney.core import AccountType, EntityKind, LegSpec, new_id
from pymoney.core import commands as c
from tests.conftest import Chart


class TestStoreContract:
    def test_change_log_view_indexing_and_repr(self, funded: Chart):
        log = funded.book.changelog
        assert log[0].revision == 1
        assert log[-1].revision == log.head
        assert f"head=r{log.head}" in repr(log)
        assert len(log) == len(list(log))

    def test_stored_book_converts_to_settings(self, book):
        row = book.store.load_book()
        settings = row.as_settings()
        assert settings.id == row.id
        assert settings.name == row.name
        assert settings.base_currency_id == row.base_currency_id

    def test_deleting_what_is_not_there_reports_false(self, book):
        book.store.begin()
        try:
            assert not book.store.delete(EntityKind.ACCOUNT, new_id())
            assert not book.store.delete(EntityKind.PAYEE, new_id())
            assert not book.store.delete(EntityKind.PRICE, new_id())
            assert not book.store.delete(EntityKind.TRANSACTION, new_id())
        finally:
            book.store.rollback()

    def test_exists(self, funded: Chart):
        book = funded.book
        transaction = book.transactions()[0]
        assert book.store.exists(EntityKind.TRANSACTION, transaction.id)
        assert not book.store.exists(EntityKind.TRANSACTION, new_id())
        assert book.store.exists(EntityKind.ACCOUNT, funded.checking.id)
        assert not book.store.exists(EntityKind.ACCOUNT, new_id())

    def test_balances_of_no_accounts_is_empty(self, book):
        assert book.store.balances_by_account(account_ids=[]) == {}

    def test_balances_can_be_scoped_to_accounts(self, funded: Chart):
        book = funded.book
        scoped = book.store.balances_by_account(account_ids=[funded.checking.id])
        assert set(scoped) == {funded.checking.id}
        assert scoped[funded.checking.id] == 1311580

    def test_repr_is_informative(self, book):
        assert repr(book.store).startswith(("MemoryStore(", "SqlStore("))

    def test_repr_never_leaks_a_password(self, book):
        assert "secret" not in repr(book.store)

    def test_changes_since_beyond_the_head_is_empty(self, funded: Chart):
        book = funded.book
        assert book.store.changes_since(book.revision) == ()
        assert book.store.changes_since(book.revision, max_revisions=5) == ()

    def test_changes_since_respects_the_revision_cap(self, funded: Chart):
        """A paged sync must not drag the whole log back for one page."""
        book = funded.book
        capped = book.store.changes_since(0, max_revisions=3)
        assert {c.revision for c in capped} == {1, 2, 3}

    def test_the_cap_never_truncates_a_revision(self, funded: Chart):
        """Whole revisions or nothing: half a command is not a valid state."""
        book = funded.book
        target = book.add_account("Food", AccountType.EXPENSE, parent=funded.expenses)
        book.execute(c.MergeAccounts(source_id=funded.groceries.id, target_id=target.id))
        merge_revision = book.revision
        full = book.store.changes_at(merge_revision)
        assert len(full) > 1
        capped = book.store.changes_since(merge_revision - 1, max_revisions=1)
        assert len(capped) == len(full)

    def test_find_imported_prefix(self, funded: Chart):
        """Lets an importer detect "this book has already had a file loaded"
        before it starts creating accounts."""
        book = funded.book
        assert book.store.find_imported_prefix("kmm:") is None
        book.post(
            date(2026, 3, 1),
            [
                LegSpec.of(funded.checking.id, "-1.00"),
                LegSpec.of(funded.fuel.id),
            ],
            imported_id="kmm:T0001",
        )
        assert book.store.find_imported_prefix("kmm:") == "kmm:T0001"
        assert book.store.find_imported_prefix("ofx:") is None

    def test_transaction_ids_and_count_agree(self, funded: Chart):
        book = funded.book
        assert len(book.store.transaction_ids()) == book.store.transaction_count()

    def test_posting_count_of_an_unused_account(self, funded: Chart):
        assert funded.book.store.posting_count(funded.savings.id) == 0

    def test_children_ids_of_an_unknown_parent_is_empty(self, book):
        assert book.store.children_ids(new_id()) == ()

    def test_close_releases_resources(self, book):
        book.close()


class TestChangeLogDiscard:
    """The one exception to append-only, and its guard."""

    def test_discarding_trailing_entries(self):
        from datetime import datetime, timezone

        from pymoney.core import Change, ChangeLog, ChangeOp

        def make(revision: int) -> Change:
            return Change(
                revision=revision,
                at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                kind=EntityKind.ACCOUNT,
                entity_id=new_id(),
                op=ChangeOp.CREATE,
            )

        log = ChangeLog([make(1), make(2), make(3)])
        log.discard_after(1)
        assert [c.revision for c in log] == [1]

    def test_it_cannot_be_used_to_grow_the_log(self):
        from pymoney.core import ChangeLog

        log = ChangeLog()
        with pytest.raises(ValueError, match="cannot grow"):
            log.discard_after(5)


class TestSyncRowCap:
    def test_the_row_cap_stops_after_the_first_oversized_batch(self, funded: Chart):
        """`max_rows` is soft: it stops paging, it never splits a revision."""
        book = funded.book
        page = book.pull(0, max_rows=1)
        assert len(page) == 1
        assert not page.complete
        # ...and paging from there continues where it left off.
        nxt = book.pull(page.to_revision, max_rows=1)
        assert nxt.batches[0].revision == page.to_revision + 1

class TestStoreContract:
    """Behaviour every Store must share, asserted against each backend.

    These use the ``--stores`` parametrised fixtures from conftest rather than
    this module's SQL-only ones, because they are about the contract, not about
    any one dialect.
    """

    def test_change_log_view_indexing_and_repr(self, funded):
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
        from pymoney.core import EntityKind

        book.store.begin()
        try:
            assert not book.store.delete(EntityKind.ACCOUNT, new_id())
            assert not book.store.delete(EntityKind.PRICE, new_id())
        finally:
            book.store.rollback()

    def test_balances_of_no_accounts_is_empty(self, book):
        assert book.store.balances_by_account(account_ids=[]) == {}

    def test_repr(self, book):
        assert "transactions" in repr(book.store) or "book=" in repr(book.store)

    def test_changes_since_beyond_the_head_is_empty(self, funded):
        book = funded.book
        assert book.store.changes_since(book.revision) == ()
        assert book.store.changes_since(book.revision, max_revisions=5) == ()

    def test_changes_since_respects_the_revision_cap(self, funded):
        book = funded.book
        capped = book.store.changes_since(0, max_revisions=3)
        assert len({c.revision for c in capped}) == 3
        assert {c.revision for c in capped} == {1, 2, 3}

    def test_close_releases_resources(self, book):
        book.close()

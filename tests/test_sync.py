"""The change log, revisions, idempotency and atomicity.

These are the tests that the future replica protocol and HTTP API will lean on:
if a client can trust ``revision`` and ``changes_since``, it can cache safely.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from pymoney.core import (
    Account,
    AccountType,
    Change,
    ChangeLog,
    ChangeOp,
    Command,
    CommandError,
    EntityKind,
    LegSpec,
    NotFoundError,
    Price,
    ValidationError,
    new_id,
)
from pymoney.core import (
    commands as c,
)
from pymoney.core.book import Book
from pymoney.core.handlers import HANDLERS
from pymoney.core.handlers.transactions import post_transaction
from tests.conftest import Chart, corrupt


class TestChangeLog:
    def make(self, revision: int, op=ChangeOp.CREATE, entity_id=None) -> Change:
        from datetime import datetime, timezone

        return Change(
            revision=revision,
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            kind=EntityKind.ACCOUNT,
            entity_id=entity_id or new_id(),
            op=op,
        )

    def test_append_and_head(self):
        log = ChangeLog()
        assert log.head == 0
        assert len(log) == 0
        log.append(self.make(1))
        log.append(self.make(2))
        assert log.head == 2
        assert len(log) == 2

    def test_is_append_only(self):
        log = ChangeLog()
        log.append(self.make(5))
        with pytest.raises(ValueError, match="append-only"):
            log.append(self.make(4))

    def test_several_changes_may_share_a_revision(self):
        log = ChangeLog()
        log.extend([self.make(1), self.make(1)])
        assert len(log.at(1)) == 2

    def test_since(self):
        log = ChangeLog([self.make(1), self.make(2), self.make(3)])
        assert len(log.since(0)) == 3
        assert len(log.since(2)) == 1
        assert log.since(3) == ()

    def test_for_entity(self):
        target = new_id()
        log = ChangeLog(
            [self.make(1, entity_id=target), self.make(2), self.make(3, entity_id=target)]
        )
        assert len(log.for_entity(target)) == 2

    def test_tombstones(self):
        log = ChangeLog([self.make(1), self.make(2, ChangeOp.DELETE), self.make(3)])
        assert len(log.tombstones()) == 1
        assert log.tombstones(since=2) == ()

    def test_indexing_and_iteration(self):
        log = ChangeLog([self.make(1), self.make(2)])
        assert log[0].revision == 1
        assert [ch.revision for ch in log] == [1, 2]

    def test_repr_and_str(self):
        log = ChangeLog([self.make(7)])
        assert "head=r7" in repr(log)
        assert str(log[0]).startswith("r7 CREATE ACCOUNT")

    def test_is_delete(self):
        assert self.make(1, ChangeOp.DELETE).is_delete
        assert not self.make(1).is_delete


class TestRevisions:
    def test_a_fresh_book_is_at_one(self, book: Book):
        """r0 means "no book yet"; creating it is the first command."""
        assert book.revision == 1

    def test_one_command_is_one_revision(self, book: Book):
        first = book.add_currency("USD")
        assert book.revision == 2
        book.add_account("Assets", AccountType.ASSET)
        assert book.revision == 3
        assert first.revision == 2

    def test_entities_carry_the_revision_that_created_them(self, book: Book):
        account = book.add_account("Assets", AccountType.ASSET)
        assert account.revision == book.revision

    def test_updates_bump_the_revision(self, chart: Chart):
        book = chart.book
        before = book.revision
        updated = book.execute(
            c.RenameAccount(account_id=chart.fuel.id, name="Petrol")
        ).entity
        assert updated.revision == before + 1

    def test_the_changelog_head_tracks_the_revision(self, funded: Chart):
        assert funded.book.changelog.head == funded.book.revision

    def test_a_rejected_command_does_not_burn_a_revision(self, chart: Chart):
        """Clients poll by revision; a wasted bump means a pointless re-sync."""
        book = chart.book
        before = book.revision
        with pytest.raises(NotFoundError):
            book.execute(c.DeleteTransaction(transaction_id=new_id()))
        assert book.revision == before
        assert book.changelog.head == before


class TestChangeRecords:
    def test_create_is_recorded(self, book: Book):
        result = book.execute(c.CreateAccount(name="Assets", type=AccountType.ASSET))
        change = result.changes[0]
        assert change.op is ChangeOp.CREATE
        assert change.kind is EntityKind.ACCOUNT
        assert change.entity_id == result.entity.id
        assert change.command == "CreateAccount"
        assert change.revision == book.revision

    def test_the_actor_is_recorded(self, book: Book):
        result = book.execute(
            c.CreateAccount(name="Assets", type=AccountType.ASSET, actor="alice")
        )
        assert result.changes[0].actor == "alice"

    def test_the_command_id_is_recorded(self, book: Book):
        key = new_id()
        result = book.execute(
            c.CreateAccount(name="Assets", type=AccountType.ASSET, command_id=key)
        )
        assert result.changes[0].command_id == key

    def test_the_timestamp_comes_from_the_clock(self, book: Book, clock):
        clock.advance(hours=3)
        result = book.execute(c.CreateAccount(name="Assets", type=AccountType.ASSET))
        assert result.changes[0].at == clock.now()

    def test_changes_since_is_the_pull_half_of_sync(self, funded: Chart):
        book = funded.book
        watermark = book.revision
        book.post(
            date(2026, 2, 1),
            [LegSpec.of(funded.checking.id, "-9.99"), LegSpec.of(funded.fuel.id)],
        )
        delta = book.changes_since(watermark)
        assert len(delta) == 1
        assert delta[0].kind is EntityKind.TRANSACTION
        assert book.changes_since(book.revision) == ()

    def test_a_client_can_resolve_a_change_to_an_entity(self, funded: Chart):
        book = funded.book
        watermark = book.revision
        transaction = book.post(
            date(2026, 2, 1),
            [LegSpec.of(funded.checking.id, "-9.99"), LegSpec.of(funded.fuel.id)],
        )
        change = book.changes_since(watermark)[0]
        assert book.entity_for_change(change).id == transaction.id

    def test_a_deleted_entity_resolves_to_none(self, funded: Chart):
        book = funded.book
        transaction = book.transactions(account_id=funded.card.id)[0]
        result = book.execute(c.DeleteTransaction(transaction_id=transaction.id))
        assert book.entity_for_change(result.changes[0]) is None

    def test_prices_resolve_too(self, chart: Chart):
        book = chart.book
        result = book.execute(
            c.AddPrice(
                base_id=chart.usd.id,
                quote_id=chart.eur.id,
                rate="0.9",
                effective_date=date(2026, 1, 31),
            )
        )
        change = result.changes[0]
        assert change.kind is EntityKind.PRICE
        assert book.entity_for_change(change).id == result.entity.id

    def test_snapshot_ids_let_a_replica_detect_drift(self, funded: Chart):
        snapshot = funded.book.snapshot_ids()
        assert len(snapshot["transactions"]) == 4
        assert len(snapshot["accounts"]) == 18
        assert snapshot["prices"] == ()


class TestIdempotency:
    def test_replaying_a_command_id_is_a_no_op(self, chart: Chart):
        book = chart.book
        key = new_id()
        command = c.CreateAccount(
            name="Holiday Fund",
            type=AccountType.SAVINGS,
            parent_id=chart.assets.id,
            command_id=key,
        )
        first = book.execute(command)
        revision = book.revision
        second = book.execute(command)
        assert second.replayed
        assert not first.replayed
        assert second.entity.id == first.entity.id
        assert book.revision == revision
        assert len([a for a in book.accounts() if a.name == "Holiday Fund"]) == 1

    def test_a_retried_post_does_not_double_book(self, chart: Chart):
        """The mobile-client-on-a-train case."""
        book = chart.book
        command = c.PostTransaction(
            date=date(2026, 1, 10),
            legs=(
                LegSpec.of(chart.checking.id, "-42.50"),
                LegSpec.of(chart.groceries.id),
            ),
            command_id=new_id(),
        )
        book.execute(command)
        book.execute(command)
        book.execute(command)
        assert len(book.transactions()) == 1
        assert book.balance(chart.checking.id).minor == -4250

    def test_the_replay_returns_the_original_changes(self, chart: Chart):
        book = chart.book
        command = c.CreateAccount(name="X", type=AccountType.ASSET, command_id=new_id())
        first = book.execute(command)
        replay = book.execute(command)
        assert replay.revision == first.revision
        assert replay.changes == first.changes

    def test_commands_without_a_key_are_not_deduplicated(self, chart: Chart):
        book = chart.book
        for _ in range(2):
            book.post(
                date(2026, 1, 10),
                [
                    LegSpec.of(chart.checking.id, "-42.50"),
                    LegSpec.of(chart.groceries.id),
                ],
            )
        assert len(book.transactions()) == 2

    def test_a_failed_command_is_not_cached(self, chart: Chart):
        """A retry after fixing the data must be allowed to succeed."""
        book = chart.book
        key = new_id()
        with pytest.raises(NotFoundError):
            book.execute(
                c.CreateAccount(
                    name="X",
                    type=AccountType.ASSET,
                    commodity_id=new_id(),
                    command_id=key,
                )
            )
        account = book.execute(
            c.CreateAccount(name="X", type=AccountType.ASSET, command_id=key)
        ).entity
        assert account.name == "X"


class TestDispatch:
    def test_unknown_command(self, book: Book):
        class Mystery(Command):
            pass

        with pytest.raises(CommandError, match="no handler"):
            book.execute(Mystery())

    def test_execute_all(self, book: Book):
        results = book.execute_all(
            [
                c.CreateCommodity(code="USD"),
                c.CreateAccount(name="Assets", type=AccountType.ASSET),
            ]
        )
        assert [r.revision for r in results] == [2, 3]

    def test_command_name(self):
        command = c.CreateAccount(name="X", type=AccountType.ASSET)
        # ``name`` is a field here, so the log uses ``command_name``
        assert command.command_name == "CreateAccount"
        assert command.name == "X"

    def test_result_entity_id(self, book: Book):
        result = book.execute(c.CreateAccount(name="Assets", type=AccountType.ASSET))
        assert result.entity_id == result.entity.id

    def test_result_entity_id_is_none_without_an_entity(self):
        from pymoney.core import CommandResult

        assert CommandResult(revision=1, command="X").entity_id is None

    def test_unset_is_falsey(self):
        from pymoney.core import UNSET

        assert not UNSET
        assert repr(UNSET) == "UNSET"


class TestAtomicity:
    """A handler that fails halfway must leave no trace (decision #6).

    Rollback is exercised by registering throwaway commands that mutate and then
    raise.  Real handlers validate before they mutate, so these paths cannot be
    reached through the public command set -- which is exactly why they need a
    test of their own: the undo log is the safety net for the next handler
    somebody writes.
    """

    @pytest.fixture
    def sabotage(self):
        """Run an arbitrary mutation inside ``execute`` and then fail."""
        registered: list[type] = []

        def run(book: Book, mutate, error=RuntimeError("boom")):
            class Sabotage(Command):
                pass

            def handler(bk: Book, command):
                mutate(bk)
                raise error

            HANDLERS[Sabotage] = handler
            registered.append(Sabotage)
            return book.execute(Sabotage())

        try:
            yield run
        finally:
            for command_type in registered:
                HANDLERS.pop(command_type, None)

    def test_a_rolled_back_update_restores_the_old_row(self, chart: Chart, sabotage):
        book = chart.book
        before_revision = book.revision
        before = book.account(chart.fuel.id)

        with pytest.raises(RuntimeError, match="boom"):
            sabotage(
                book,
                lambda bk: bk.put(
                    EntityKind.ACCOUNT,
                    replace(bk.account(chart.fuel.id), description="scribbled"),
                ),
            )

        assert book.revision == before_revision
        assert book.account(chart.fuel.id) == before
        assert book.changelog.head == before_revision

    def test_a_rolled_back_create_leaves_no_row(self, chart: Chart, sabotage):
        """Covers the undo of an insert, including the derived indexes."""
        book = chart.book
        new_account_id = new_id()
        before_children = book.children(chart.expenses.id)

        def mutate(bk: Book) -> None:
            bk.put(
                EntityKind.ACCOUNT,
                Account(
                    id=new_account_id,
                    book_id=bk.id,
                    type=AccountType.EXPENSE,
                    name="Ghost",
                    commodity=bk.base_currency,
                    parent_id=chart.expenses.id,
                ),
                created=True,
            )

        with pytest.raises(RuntimeError):
            sabotage(book, mutate)

        with pytest.raises(NotFoundError):
            book.account(new_account_id)
        assert book.children(chart.expenses.id) == before_children
        assert book.check_integrity() == ()

    def test_a_rolled_back_transaction_insert_clears_the_index(
        self, chart: Chart, sabotage
    ):
        book = chart.book

        def mutate(bk: Book) -> None:
            post_transaction(
                bk,
                c.PostTransaction(
                    date=date(2026, 1, 9),
                    legs=(
                        LegSpec.of(chart.checking.id, "-5.00"),
                        LegSpec.of(chart.fuel.id),
                    ),
                )
            )

        with pytest.raises(RuntimeError):
            sabotage(book, mutate)

        assert book.transactions() == ()
        assert book.transactions(account_id=chart.checking.id) == ()
        assert book.balance(chart.checking.id).is_zero

    def test_a_rolled_back_delete_restores_the_row(self, chart: Chart, sabotage):
        book = chart.book
        payee = book.add_payee("Shop")
        with pytest.raises(RuntimeError):
            sabotage(book, lambda bk: bk.drop(EntityKind.PAYEE, payee.id))
        assert book.payee(payee.id).name == "Shop"

    def test_a_rolled_back_price_insert_leaves_no_price(self, chart: Chart, sabotage):
        book = chart.book
        before = len(book.prices)

        def mutate(bk: Book) -> None:
            bk.put(
                EntityKind.PRICE,
                Price.of(
                    chart.usd,
                    bk.base_currency,
                    "1.5",
                    effective_date=date(2026, 1, 1),
                    as_of=bk.clock.now(),
                ),
                created=True,
            )

        with pytest.raises(RuntimeError):
            sabotage(book, mutate)
        assert len(book.prices) == before

    def test_a_rolled_back_price_update_restores_the_old_rate(
        self, priced: Chart, sabotage
    ):
        book = priced.book
        before = {p.id: p.rate for p in book.prices.all()}
        with pytest.raises(RuntimeError):
            sabotage(
                book,
                lambda bk: bk.put(
                    EntityKind.PRICE, replace(bk.prices.all()[0], num=999, den=1)
                ),
            )
        assert {p.id: p.rate for p in book.prices.all()} == before

    def test_a_rolled_back_price_delete_restores_the_price(self, priced: Chart, sabotage):
        book = priced.book
        target = book.prices.all()[0]
        with pytest.raises(RuntimeError):
            sabotage(book, lambda bk: bk.drop(EntityKind.PRICE, target.id))
        assert book.prices.get(target.id).rate == target.rate

    def test_dropping_a_missing_price_is_an_error(self, chart: Chart, sabotage):
        with pytest.raises(NotFoundError, match="no price"):
            sabotage(
                chart.book,
                lambda bk: bk.drop(EntityKind.PRICE, new_id()),
            )

    def test_dropping_a_missing_row_is_an_error(self, chart: Chart, sabotage):
        with pytest.raises(NotFoundError, match="no payee"):
            sabotage(
                chart.book,
                lambda bk: bk.drop(EntityKind.PAYEE, new_id()),
            )

    def test_mutations_outside_execute_are_refused(self, chart: Chart):
        """Only the command path may write (decision #14, applied in-process)."""
        book = chart.book
        with pytest.raises(AssertionError, match="inside execute"):
            book.put(EntityKind.PAYEE, book.add_payee("X"))
        with pytest.raises(AssertionError, match="inside execute"):
            book.drop(EntityKind.PAYEE, new_id())


class TestIntegrity:
    def test_a_healthy_book_reports_nothing(self, priced: Chart):
        assert priced.book.check_integrity() == ()

    def test_a_dangling_parent_is_detected(self, chart: Chart):
        book = chart.book
        corrupt(
            book,
            EntityKind.ACCOUNT,
            replace(book.account(chart.fuel.id), parent_id=new_id()),
        )
        assert any("missing parent" in p for p in book.check_integrity())

    def test_a_class_mismatch_is_detected(self, chart: Chart):
        from dataclasses import replace

        book = chart.book
        corrupt(
            book,
            EntityKind.ACCOUNT,
            replace(book.account(chart.fuel.id), parent_id=chart.assets.id),
        )
        assert any("ASSET parent" in p for p in book.check_integrity())

    def test_an_unknown_commodity_is_detected(self, chart: Chart):
        from dataclasses import replace

        from pymoney.core import Commodity

        book = chart.book
        corrupt(
            book,
            EntityKind.ACCOUNT,
            replace(book.account(chart.fuel.id), commodity=Commodity.currency("ZZZ")),
        )
        assert any("unknown commodity" in p for p in book.check_integrity())

    def test_an_unbalanced_transaction_is_detected(self, funded: Chart):
        from dataclasses import replace

        from pymoney.core import Amount

        book = funded.book
        transaction = book.transactions()[0]
        broken = replace(
            transaction,
            splits=(
                replace(
                    transaction.splits[0],
                    value=Amount(funded.eur, 1),
                    quantity=Amount(funded.eur, 1),
                ),
                transaction.splits[1],
            ),
        )
        corrupt(book, EntityKind.TRANSACTION, broken)
        assert any("sum to zero" in p for p in book.check_integrity())

    def test_a_missing_account_reference_is_detected(self, funded: Chart):
        from dataclasses import replace

        book = funded.book
        transaction = book.transactions()[0]
        corrupt(
            book,
            EntityKind.TRANSACTION,
            replace(
                transaction,
                splits=(
                    replace(transaction.splits[0], account_id=new_id()),
                    transaction.splits[1],
                ),
            ),
        )
        assert any("missing account" in p for p in book.check_integrity())

    def test_a_commodity_mismatch_on_a_split_is_detected(self, funded: Chart):
        from dataclasses import replace

        from pymoney.core import Amount

        book = funded.book
        transaction = book.transactions()[0]
        corrupt(
            book,
            EntityKind.TRANSACTION,
            replace(
                transaction,
                splits=(
                    replace(
                        transaction.splits[0],
                        quantity=Amount(
                            funded.usd, transaction.splits[0].quantity.minor
                        ),
                    ),
                    transaction.splits[1],
                ),
            ),
        )
        problems = book.check_integrity()
        assert any("posts USD to a EUR account" in p for p in problems)

    def test_a_missing_payee_reference_is_detected(self, funded: Chart):
        from dataclasses import replace

        book = funded.book
        transaction = book.transactions()[0]
        corrupt(
            book, EntityKind.TRANSACTION, replace(transaction, payee_id=new_id())
        )
        assert any("missing payee" in p for p in book.check_integrity())


class TestRevisionBatch:
    """One revision is one command, and must be applied all-or-nothing."""

    def test_a_single_row_command(self, chart: Chart):
        book = chart.book
        result = book.execute(c.RenameAccount(account_id=chart.fuel.id, name="Petrol"))
        batch = book.revision_batch(result.revision)
        assert batch.revision == result.revision
        assert batch.command == "RenameAccount"
        assert batch.rows == 1
        assert len(batch) == 1
        assert batch.upserts[0].name == "Petrol"
        assert batch.deletions == ()
        assert batch.kinds == (EntityKind.ACCOUNT,)

    def test_a_merge_arrives_whole(self, funded: Chart):
        """400 transactions repointed is one fact, not 400."""
        book = funded.book
        target = book.add_account("Food", AccountType.EXPENSE, parent=funded.expenses)
        result = book.execute(
            c.MergeAccounts(source_id=funded.groceries.id, target_id=target.id)
        )
        batch = book.revision_batch(result.revision)
        assert batch.command == "MergeAccounts"
        assert batch.kinds == (EntityKind.ACCOUNT, EntityKind.TRANSACTION)
        assert len(batch.upserts_of(EntityKind.TRANSACTION)) == 1
        assert batch.deletions_of(EntityKind.ACCOUNT) == (funded.groceries.id,)
        assert batch.rows == len(batch.upserts) + len(batch.deletions)

    def test_entities_are_resolved_not_just_referenced(self, funded: Chart):
        book = funded.book
        transaction = book.post(
            date(2026, 2, 1),
            [LegSpec.of(funded.checking.id, "-5.00"), LegSpec.of(funded.fuel.id)],
            memo="Later",
        )
        batch = book.revision_batch(book.revision)
        assert batch.upserts[0].id == transaction.id
        assert batch.upserts[0].memo == "Later"

    def test_deletions_carry_tombstones(self, funded: Chart):
        book = funded.book
        target = book.transactions(account_id=funded.card.id)[0]
        result = book.execute(c.DeleteTransaction(transaction_id=target.id))
        batch = book.revision_batch(result.revision)
        assert batch.upserts == ()
        assert batch.deletions_of(EntityKind.TRANSACTION) == (target.id,)
        assert batch.deletions[0].op is ChangeOp.DELETE

    def test_an_entity_touched_twice_appears_once(self, chart: Chart):
        """Applying a batch twice must be the same as applying it once."""
        book = chart.book
        payee = book.add_payee("Shop")
        other = book.add_payee("Shoppe")
        book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.checking.id, "-10.00", payee_id=other.id),
                LegSpec.of(chart.groceries.id),
            ],
            payee_id=other.id,
        )
        result = book.execute(c.MergePayees(source_ids=(other.id,), target_id=payee.id))
        batch = book.revision_batch(result.revision)
        transaction_upserts = batch.upserts_of(EntityKind.TRANSACTION)
        assert len(transaction_upserts) == 1
        assert len({e.id for e in batch.upserts}) == len(batch.upserts)

    def test_book_settings_changes_are_batched_too(self, chart: Chart):
        book = chart.book
        result = book.execute(c.SetBaseCurrency(commodity_id=chart.usd.id))
        batch = book.revision_batch(result.revision)
        assert batch.kinds == (EntityKind.BOOK,)
        assert batch.upserts[0].base_currency_id == chart.usd.id

    def test_metadata_is_carried(self, chart: Chart):
        book = chart.book
        key = new_id()
        result = book.execute(
            c.RenameAccount(
                account_id=chart.fuel.id,
                name="Petrol",
                command_id=key,
                actor="alice",
            )
        )
        batch = book.revision_batch(result.revision)
        assert batch.command_id == key
        assert batch.actor == "alice"
        assert batch.at == book.clock.now()

    def test_unknown_revision(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no revision"):
            chart.book.revision_batch(9999)

    def test_a_revision_that_was_never_issued(self, chart: Chart):
        """No-op commands do not create revisions, so there are no holes."""
        with pytest.raises(NotFoundError):
            chart.book.revision_batch(0)

    def test_str(self, chart: Chart):
        book = chart.book
        result = book.execute(c.RenameAccount(account_id=chart.fuel.id, name="Petrol"))
        assert str(book.revision_batch(result.revision)) == (
            f"r{result.revision} RenameAccount: 1 upsert(s), 0 deletion(s)"
        )

    def test_build_batch_rejects_an_empty_revision(self):
        from pymoney.core.sync import build_batch

        with pytest.raises(ValueError, match="no changes"):
            build_batch(1, [], lambda ch: None)


class TestPull:
    """``pull`` is the shape the GET /changes endpoint should have."""

    def test_a_full_pull_from_scratch(self, funded: Chart):
        book = funded.book
        page = book.pull()
        assert page.from_revision == 0
        assert page.to_revision == book.revision
        assert page.head == book.revision
        assert page.complete
        assert len(page) == book.revision
        assert not page.is_empty

    def test_an_incremental_pull(self, funded: Chart):
        book = funded.book
        watermark = book.revision
        book.post(
            date(2026, 2, 1),
            [LegSpec.of(funded.checking.id, "-5.00"), LegSpec.of(funded.fuel.id)],
        )
        page = book.pull(watermark)
        assert len(page) == 1
        assert page.complete
        assert page.batches[0].command == "PostTransaction"

    def test_a_caught_up_client_gets_an_empty_page(self, funded: Chart):
        book = funded.book
        page = book.pull(book.revision)
        assert page.is_empty
        assert page.complete
        assert page.to_revision == book.revision
        assert page.rows == 0

    def test_revision_cap(self, funded: Chart):
        book = funded.book
        page = book.pull(0, max_revisions=3)
        assert len(page) == 3
        assert not page.complete
        assert page.head == book.revision
        assert page.head - page.to_revision > 0

    def test_a_row_cap_never_splits_a_revision(self, funded: Chart):
        """The whole point: half a merge is a state the server was never in."""
        book = funded.book
        target = book.add_account("Food", AccountType.EXPENSE, parent=funded.expenses)
        watermark = book.revision
        book.execute(c.MergeAccounts(source_id=funded.groceries.id, target_id=target.id))
        page = book.pull(watermark, max_rows=1)
        assert len(page) == 1
        batch = page.batches[0]
        assert batch.rows > 1  # the cap was exceeded rather than the batch split
        assert batch.deletions_of(EntityKind.ACCOUNT) == (funded.groceries.id,)
        assert page.complete

    def test_paging_to_completion_covers_everything_exactly_once(self, funded: Chart):
        book = funded.book
        seen: list[int] = []
        watermark = 0
        for _ in range(100):
            page = book.pull(watermark, max_rows=2)
            if page.is_empty:
                break
            seen.extend(b.revision for b in page.batches)
            watermark = page.to_revision
            if page.complete:
                break
        assert seen == sorted(seen)
        assert len(seen) == len(set(seen))
        assert seen == sorted({ch.revision for ch in book.changelog})

    def test_changes_flattens_back_to_the_raw_log(self, funded: Chart):
        book = funded.book
        page = book.pull()
        assert page.changes == tuple(book.changelog)

    def test_a_watermark_ahead_of_the_book_is_refused(self, funded: Chart):
        """The client is on the wrong book, or the server was restored."""
        book = funded.book
        with pytest.raises(ValidationError, match="ahead of this book"):
            book.pull(book.revision + 1)

    def test_a_negative_watermark_is_refused(self, funded: Chart):
        with pytest.raises(ValidationError, match="must not be negative"):
            funded.book.pull(-1)

    def test_str(self, funded: Chart):
        book = funded.book
        full = book.pull()
        assert f"r0..r{book.revision} of r{book.revision}" in str(full)
        partial = book.pull(0, max_revisions=1)
        assert "behind by" in str(partial)

    def test_a_replica_rebuilt_from_batches_matches_the_book(self, priced: Chart):
        """End to end: apply whole batches and compare against the source."""
        book = priced.book
        replica: dict = {}
        watermark = 0
        while True:
            page = book.pull(watermark, max_rows=3)
            for batch in page:
                for entity in batch.upserts:
                    replica[entity.id] = entity
                for tombstone in batch.deletions:
                    replica.pop(tombstone.entity_id, None)
            watermark = page.to_revision
            if page.complete:
                break

        snapshot = book.snapshot_ids()
        expected = (
            set(snapshot["commodities"])
            | set(snapshot["accounts"])
            | set(snapshot["payees"])
            | set(snapshot["transactions"])
            | set(snapshot["prices"])
        )
        # the settings row is addressed by the book's own id
        assert set(replica) - {book.id} == expected
        assert replica[book.id].name == book.name
        for account in book.accounts():
            assert replica[account.id].name == account.name
        for transaction in book.transactions():
            assert replica[transaction.id].splits == transaction.splits

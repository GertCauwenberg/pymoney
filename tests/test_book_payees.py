"""Book: payees and payee merges (decision #13)."""

from __future__ import annotations

from datetime import date

import pytest

from pymoney.core import (
    ConflictError,
    LegSpec,
    NotFoundError,
    new_id,
)
from pymoney.core import (
    commands as c,
)
from tests.conftest import Chart


class TestPayees:
    def test_create(self, book):
        payee = book.add_payee("Supermarket", notes="corner shop", address="Main St")
        assert payee.name == "Supermarket"
        assert payee.notes == "corner shop"
        assert book.payee(payee.id) == payee

    def test_names_are_unique(self, book):
        book.add_payee("Supermarket")
        with pytest.raises(ConflictError, match="already exists"):
            book.add_payee("supermarket")

    def test_a_match_key_also_counts_as_taken(self, book):
        book.add_payee("Amazon", match_keys=("AMZN",))
        with pytest.raises(ConflictError, match="already exists as 'Amazon'"):
            book.add_payee("amzn")

    def test_find_by_name_or_match_key(self, book):
        payee = book.add_payee("Amazon", match_keys=("AMAZON EU",))
        assert book.find_payee("amazon").id == payee.id
        assert book.find_payee("AMAZON EU").id == payee.id
        assert book.find_payee("nobody") is None

    def test_unknown_payee(self, book):
        with pytest.raises(NotFoundError, match="no payee"):
            book.payee(new_id())

    def test_payees_are_sorted(self, book):
        book.add_payee("Zoo")
        book.add_payee("Aquarium")
        assert [p.name for p in book.payees()] == ["Aquarium", "Zoo"]

    def test_default_account_is_validated(self, book):
        with pytest.raises(NotFoundError, match="no account"):
            book.add_payee("X", default_account_id=new_id())

    def test_default_account_is_kept(self, chart: Chart):
        payee = chart.book.add_payee("Shell", default_account_id=chart.fuel.id)
        assert payee.default_account_id == chart.fuel.id

    def test_explicit_id_must_be_unique(self, book):
        payee = book.add_payee("A")
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(c.CreatePayee(name="B", id=payee.id))

    def test_update(self, book):
        payee = book.add_payee("Shell")
        updated = book.execute(
            c.UpdatePayee(
                payee_id=payee.id,
                name="Shell Station",
                notes="n",
                address="a",
                match_keys=("SHELL",),
            )
        ).entity
        assert updated.name == "Shell Station"
        assert updated.match_keys == ("SHELL",)

    def test_update_to_a_taken_name(self, book):
        book.add_payee("A")
        b = book.add_payee("B")
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(c.UpdatePayee(payee_id=b.id, name="a"))

    def test_update_to_its_own_name_is_allowed(self, book):
        payee = book.add_payee("A")
        assert (
            book.execute(
                c.UpdatePayee(payee_id=payee.id, name="A", notes="x")
            ).entity.notes
            == "x"
        )

    def test_update_validates_the_default_account(self, book):
        payee = book.add_payee("A")
        with pytest.raises(NotFoundError, match="no account"):
            book.execute(c.UpdatePayee(payee_id=payee.id, default_account_id=new_id()))

    def test_no_op_update(self, book):
        payee = book.add_payee("A")
        before = book.revision
        assert book.execute(c.UpdatePayee(payee_id=payee.id, name="A")).revision == before

    def test_delete(self, book):
        payee = book.add_payee("A")
        book.execute(c.DeletePayee(payee_id=payee.id))
        with pytest.raises(NotFoundError):
            book.payee(payee.id)

    def test_cannot_delete_a_payee_in_use(self, chart: Chart):
        book = chart.book
        payee = book.add_payee("Market")
        book.post(
            date(2026, 1, 5),
            [LegSpec.of(chart.checking.id, "-10"), LegSpec.of(chart.groceries.id)],
            payee_id=payee.id,
        )
        with pytest.raises(ConflictError, match="merge it instead"):
            book.execute(c.DeletePayee(payee_id=payee.id))

    def test_cannot_delete_a_payee_used_on_a_split(self, chart: Chart):
        book = chart.book
        payee = book.add_payee("Market")
        book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.checking.id, "-10"),
                LegSpec.of(chart.groceries.id, payee_id=payee.id),
            ],
        )
        with pytest.raises(ConflictError):
            book.execute(c.DeletePayee(payee_id=payee.id))


class TestMergePayees:
    @pytest.fixture
    def messy(self, chart: Chart):
        """The real-world case: one shop, four spellings."""
        book = chart.book
        canonical = book.add_payee("Amazon")
        variants = [
            book.add_payee("AMAZON EU SARL"),
            book.add_payee("Amazon.de"),
            book.add_payee("AMZN Marketplace"),
        ]
        for index, payee in enumerate(variants):
            book.post(
                date(2026, 1, 5 + index),
                [
                    LegSpec.of(chart.checking.id, "-10.00"),
                    LegSpec.of(chart.groceries.id),
                ],
                payee_id=payee.id,
            )
        return canonical, variants

    def test_transactions_are_repointed(self, chart: Chart, messy):
        book = chart.book
        canonical, variants = messy
        book.execute(
            c.MergePayees(
                source_ids=tuple(p.id for p in variants), target_id=canonical.id
            )
        )
        assert len(book.transactions(payee_id=canonical.id)) == 3
        for variant in variants:
            with pytest.raises(NotFoundError):
                book.payee(variant.id)

    def test_names_become_match_keys(self, chart: Chart, messy):
        """So the next import still recognises the old spellings."""
        book = chart.book
        canonical, variants = messy
        merged = book.execute(
            c.MergePayees(
                source_ids=tuple(p.id for p in variants), target_id=canonical.id
            )
        ).entity
        assert set(merged.match_keys) == {
            "AMAZON EU SARL",
            "Amazon.de",
            "AMZN Marketplace",
        }
        assert book.find_payee("amazon.de").id == canonical.id

    def test_match_keys_can_be_dropped(self, chart: Chart, messy):
        book = chart.book
        canonical, variants = messy
        merged = book.execute(
            c.MergePayees(
                source_ids=(variants[0].id,),
                target_id=canonical.id,
                keep_match_keys=False,
            )
        ).entity
        assert merged.match_keys == ()

    def test_existing_match_keys_are_absorbed(self, chart: Chart):
        book = chart.book
        target = book.add_payee("Amazon")
        source = book.add_payee("Amazon EU", match_keys=("AMZN-EU",))
        merged = book.execute(
            c.MergePayees(source_ids=(source.id,), target_id=target.id)
        ).entity
        assert set(merged.match_keys) == {"Amazon EU", "AMZN-EU"}

    def test_split_payees_are_repointed(self, chart: Chart):
        book = chart.book
        target = book.add_payee("Amazon")
        source = book.add_payee("AMZN")
        transaction = book.post(
            date(2026, 1, 5),
            [
                LegSpec.of(chart.checking.id, "-10"),
                LegSpec.of(chart.groceries.id, payee_id=source.id),
            ],
        )
        book.execute(c.MergePayees(source_ids=(source.id,), target_id=target.id))
        updated = book.transaction(transaction.id)
        assert updated.splits[1].payee_id == target.id

    def test_one_revision_for_the_whole_merge(self, chart: Chart, messy):
        book = chart.book
        canonical, variants = messy
        before = book.revision
        result = book.execute(
            c.MergePayees(
                source_ids=tuple(p.id for p in variants), target_id=canonical.id
            )
        )
        assert result.revision == before + 1
        assert len({ch.revision for ch in result.changes}) == 1
        kinds = [ch.kind.value for ch in result.changes]
        assert kinds.count("TRANSACTION") == 3
        assert kinds.count("PAYEE") == 4  # the target update plus three deletes

    def test_untouched_transactions_are_not_rewritten(self, chart: Chart, messy):
        book = chart.book
        canonical, variants = messy
        book.post(
            date(2026, 1, 20),
            [LegSpec.of(chart.checking.id, "-5.00"), LegSpec.of(chart.fuel.id)],
        )
        result = book.execute(
            c.MergePayees(source_ids=(variants[0].id,), target_id=canonical.id)
        )
        assert [ch.kind.value for ch in result.changes].count("TRANSACTION") == 1

    def test_cannot_merge_into_itself(self, chart: Chart, messy):
        canonical, _variants = messy
        with pytest.raises(Exception, match="into itself"):
            chart.book.execute(
                c.MergePayees(source_ids=(canonical.id,), target_id=canonical.id)
            )

    def test_needs_at_least_one_source(self, chart: Chart, messy):
        canonical, _ = messy
        with pytest.raises(Exception, match="no payees to merge"):
            chart.book.execute(c.MergePayees(source_ids=(), target_id=canonical.id))

    def test_unknown_source(self, chart: Chart, messy):
        canonical, _ = messy
        with pytest.raises(NotFoundError):
            chart.book.execute(
                c.MergePayees(source_ids=(new_id(),), target_id=canonical.id)
            )

    def test_a_failed_merge_changes_nothing(self, chart: Chart, messy):
        book = chart.book
        canonical, variants = messy
        before = book.revision
        with pytest.raises(NotFoundError):
            book.execute(
                c.MergePayees(
                    source_ids=(variants[0].id, new_id()), target_id=canonical.id
                )
            )
        assert book.revision == before
        assert len(book.payees()) == 4
        assert len(book.transactions(payee_id=variants[0].id)) == 1
        assert book.check_integrity() == ()


class TestRepeatedPayeeMerges:
    def test_merging_a_name_that_is_already_a_match_key(self, chart: Chart):
        """Nothing to absorb, so the target row is not rewritten at all.

        The user had already added the alias by hand before merging the
        duplicate, which is a common order of events.
        """
        book = chart.book
        source = book.add_payee("Amazon.de")
        target = book.add_payee("Amazon")
        book.execute(c.UpdatePayee(payee_id=target.id, match_keys=("Amazon.de",)))
        result = book.execute(c.MergePayees(source_ids=(source.id,), target_id=target.id))
        assert [(ch.kind.value, ch.op.value) for ch in result.changes] == [
            ("PAYEE", "DELETE")
        ]
        assert book.payee(target.id).match_keys == ("Amazon.de",)
        assert book.find_payee("amazon.de").id == target.id

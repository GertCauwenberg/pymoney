"""Book: tags, and the tags on a transaction's legs."""

from __future__ import annotations

from datetime import date

import pytest

from pymoney.core import (
    ConflictError,
    LegSpec,
    NotFoundError,
    ValidationError,
    new_id,
)
from pymoney.core import commands as c
from pymoney.core.changelog import EntityKind
from pymoney.core.store import TransactionSearch
from tests.conftest import Chart, corrupt


class TestTags:
    def test_create_and_look_up(self, book):
        tag = book.add_tag("Holiday", colour="#ff8800", notes="Spain, May")
        assert tag.name == "Holiday"
        assert tag.colour == "#ff8800"
        assert tag.closed is False
        assert book.tag(tag.id) == tag
        assert book.find_tag("  holiday ").id == tag.id

    def test_names_are_unique_case_insensitively(self, book):
        book.add_tag("Holiday")
        with pytest.raises(ConflictError, match="already exists"):
            book.add_tag("holiday")

    def test_an_empty_name_is_refused(self, book):
        with pytest.raises(ValidationError, match="must not be empty"):
            book.add_tag("   ")

    def test_rename(self, book):
        tag = book.add_tag("Hoilday")
        book.execute(c.UpdateTag(tag_id=tag.id, name="Holiday"))
        assert book.tag(tag.id).name == "Holiday"
        assert book.find_tag("Hoilday") is None

    def test_rename_onto_another_tag_is_refused(self, book):
        book.add_tag("Work")
        hol = book.add_tag("Holiday")
        with pytest.raises(ConflictError, match="already exists"):
            book.execute(c.UpdateTag(tag_id=hol.id, name="work"))

    def test_closing_hides_it_from_the_pickers_only(self, book):
        tag = book.add_tag("Old project")
        book.execute(c.UpdateTag(tag_id=tag.id, closed=True))
        assert book.tag(tag.id).closed is True
        assert tag.id not in {t.id for t in book.tags(include_closed=False)}
        assert tag.id in {t.id for t in book.tags()}

    def test_a_no_op_update_burns_no_revision(self, book):
        tag = book.add_tag("Holiday")
        before = book.revision
        book.execute(c.UpdateTag(tag_id=tag.id, name="Holiday"))
        assert book.revision == before

    def test_delete_an_unused_tag(self, book):
        tag = book.add_tag("Spurious")
        book.execute(c.DeleteTag(tag_id=tag.id))
        assert book.find_tag("Spurious") is None

    def test_unknown_tag(self, book):
        with pytest.raises(NotFoundError, match="no tag"):
            book.tag(new_id())


class TestTagsOnSplits:
    def test_tags_ride_on_the_leg_they_were_put_on(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        reimbursable = book.add_tag("Reimbursable")
        transaction = book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id, reimbursable.id)),
            ],
        )
        spent, categorised = transaction.splits
        assert spent.tag_ids == ()
        assert categorised.tag_ids == (holiday.id, reimbursable.id)

    def test_duplicate_tag_ids_on_a_leg_collapse(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        transaction = book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(
                    chart.groceries.id, tag_ids=(holiday.id, holiday.id, holiday.id)
                ),
            ],
        )
        assert transaction.split_for(chart.groceries.id).tag_ids == (holiday.id,)

    def test_a_leg_tagged_with_an_unknown_tag_is_refused(self, chart: Chart):
        book = chart.book
        before = book.revision
        with pytest.raises(NotFoundError, match="no tag"):
            book.post(
                date(2026, 5, 18),
                [
                    LegSpec.of(chart.checking.id, "-1.00"),
                    LegSpec.of(chart.groceries.id, tag_ids=(new_id(),)),
                ],
            )
        assert book.revision == before

    def test_editing_a_transaction_replaces_its_tags(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        work = book.add_tag("Work")
        transaction = book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id,)),
            ],
        )
        book.execute(
            c.UpdateTransaction(
                transaction_id=transaction.id,
                legs=(
                    LegSpec.of(chart.checking.id, "-47.50"),
                    LegSpec.of(chart.groceries.id, tag_ids=(work.id,)),
                ),
            )
        )
        updated = book.transaction(transaction.id)
        assert updated.split_for(chart.groceries.id).tag_ids == (work.id,)
        assert book.store.transactions_for_tag(holiday.id) == ()

    def test_delete_is_refused_while_a_transaction_wears_the_tag(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id,)),
            ],
        )
        with pytest.raises(ConflictError, match="is on 1 transaction"):
            book.execute(c.DeleteTag(tag_id=holiday.id))

    def test_search_by_tag(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        work = book.add_tag("Work")
        tagged = book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id,)),
            ],
            memo="Holiday shop",
        )
        book.post(
            date(2026, 5, 19),
            [
                LegSpec.of(chart.checking.id, "-12.00"),
                LegSpec.of(chart.groceries.id),
            ],
            memo="Ordinary shop",
        )
        found = book.search(TransactionSearch(tag_id=holiday.id))
        assert [t.id for t in found] == [tagged.id]
        assert book.search(TransactionSearch(tag_id=work.id)) == ()
        # Tag and another criterion combine with AND.
        assert book.search(TransactionSearch(tag_id=holiday.id, text="ordinary")) == ()
        assert [
            t.id
            for t in book.search(TransactionSearch(tag_id=holiday.id, text="holiday"))
        ] == [tagged.id]

    def test_a_sound_ledger_with_tags_passes_the_integrity_check(self, chart: Chart):
        book = chart.book
        holiday = book.add_tag("Holiday")
        book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id,)),
            ],
        )
        assert book.check_integrity() == ()

    def test_a_missing_tag_reference_is_detected(self, chart: Chart, lax_store):
        from dataclasses import replace

        book = chart.book
        holiday = book.add_tag("Holiday")
        transaction = book.post(
            date(2026, 5, 18),
            [
                LegSpec.of(chart.checking.id, "-47.50"),
                LegSpec.of(chart.groceries.id, tag_ids=(holiday.id,)),
            ],
        )
        corrupt(
            book,
            EntityKind.TRANSACTION,
            replace(
                transaction,
                splits=(
                    transaction.splits[0],
                    replace(transaction.splits[1], tag_ids=(new_id(),)),
                ),
            ),
        )
        assert any("missing tag" in p for p in book.check_integrity())

"""Book: budgets -- one plan per account, and the plan is its months.

There is no "same every month" figure in the model.  A month with no entry is
unbudgeted, so a plan never answers for months nobody wrote down -- which is
what a stored default would do, years later, beside figures that were never
revisited.
"""

from __future__ import annotations

import pytest

from pymoney.core import NotFoundError, ValidationError
from pymoney.core import commands as c
from tests.conftest import Chart

YEAR = {f"2026-{m:02d}": "400.00" for m in range(1, 13)}


class TestBudgets:
    def test_a_plan_is_its_months(self, chart: Chart):
        book = chart.book
        budget = book.set_budget(chart.groceries.id, YEAR)
        assert budget.account_id == chart.groceries.id
        assert budget.for_month("2026-03") == 40000
        assert book.budget_for(chart.groceries.id) == budget
        assert chart.groceries.id in {b.account_id for b in book.budgets()}

    def test_a_month_nobody_planned_is_unbudgeted(self, chart: Chart):
        """Not "the last figure typed", and not zero: the question has no
        answer until someone answers it."""
        book = chart.book
        budget = book.set_budget(chart.groceries.id, YEAR)
        assert budget.for_month("2025-12") is None
        assert budget.for_month("2027-01") is None
        assert budget.amount_for("2027-01") is None

    def test_writing_months_merges_and_leaves_the_rest_alone(self, chart: Chart):
        """The period table's editors write what is on screen, so extending
        2026 must not reach back and rewrite 2025."""
        book = chart.book
        book.set_budget(chart.groceries.id, {"2025-01": "400.00", "2025-02": "400.00"})
        book.set_budget(chart.groceries.id, {"2026-01": "450.00"})
        g = book.budget_for(chart.groceries.id)
        assert g.for_month("2025-01") == 40000
        assert g.for_month("2026-01") == 45000
        assert g.for_month("2026-02") is None

    def test_replacing_makes_the_months_the_whole_plan(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, YEAR)
        book.set_budget(chart.groceries.id, {"2027-07": "500.00"}, replace=True)
        g = book.budget_for(chart.groceries.id)
        assert g.months == {"2027-07": 50000}

    def test_a_none_value_removes_just_that_month(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, YEAR)
        g = book.set_budget(chart.groceries.id, {"2026-12": None})
        assert g.for_month("2026-12") is None
        assert g.for_month("2026-11") == 40000

    def test_clearing_the_months_on_screen_clears_only_those(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, {**YEAR, "2025-01": "400.00"})
        g = book.set_budget(chart.groceries.id, dict.fromkeys(YEAR, None))
        assert g.months == {"2025-01": 40000}

    def test_clearing_the_last_month_removes_the_budget(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, {"2026-01": "400.00"})
        book.set_budget(chart.groceries.id, {"2026-01": None})
        assert book.budget_for(chart.groceries.id) is None

    def test_clearing_a_month_that_is_not_there_removes_nothing(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, {"2026-01": "400.00"})
        g = book.set_budget(chart.groceries.id, {"2026-06": None})
        assert g.months == {"2026-01": 40000}

    def test_clearing_when_there_is_no_plan_is_a_not_found(self, chart: Chart):
        with pytest.raises(NotFoundError, match="no budget"):
            chart.book.set_budget(chart.groceries.id, {"2026-01": None})

    def test_a_whole_plan_cannot_remove_a_single_month(self, chart: Chart):
        """Replacing says "this is the plan"; removals only make sense as a
        delta against one that is already there."""
        with pytest.raises(ValidationError, match="merge"):
            chart.book.set_budget(chart.groceries.id, {"2026-01": None}, replace=True)

    def test_the_inline_editor_creates_the_plan_on_first_figure(self, chart: Chart):
        book = chart.book
        assert book.budget_for(chart.fuel.id) is None
        g = book.set_budget(chart.fuel.id, {"2026-05": "120.00"})
        assert g.for_month("2026-05") == 12000
        assert g.for_month("2026-06") is None

    def test_a_no_op_set_burns_no_revision(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, YEAR)
        before = book.revision
        book.set_budget(chart.groceries.id, YEAR)
        assert book.revision == before

    def test_delete_budget(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.groceries.id, YEAR)
        book.execute(c.DeleteBudget(account_id=chart.groceries.id))
        assert book.budget_for(chart.groceries.id) is None
        with pytest.raises(NotFoundError, match="no budget"):
            book.execute(c.DeleteBudget(account_id=chart.groceries.id))

    def test_a_placeholder_cannot_be_budgeted(self, chart: Chart):
        with pytest.raises(ValidationError, match="takes no budget"):
            chart.book.set_budget(chart.expenses.id, YEAR)

    def test_a_closed_account_cannot_be_budgeted(self, chart: Chart):
        book = chart.book
        book.execute(c.SetAccountClosed(account_id=chart.fees.id, closed=True))
        with pytest.raises(ValidationError, match="is closed"):
            book.set_budget(chart.fees.id, YEAR)

    def test_a_bad_month_is_refused(self, chart: Chart):
        with pytest.raises(ValidationError, match="YYYY-MM"):
            chart.book.set_budget(chart.groceries.id, {"2026-13": "100.00"})

    def test_deleting_the_account_takes_its_plan(self, chart: Chart):
        book = chart.book
        spare = book.add_account("Spare", chart.groceries.type)
        budget = book.set_budget(spare.id, YEAR)
        book.execute(c.DeleteAccount(account_id=spare.id))
        with pytest.raises(NotFoundError):
            book.budget(budget.id)

    def test_merging_an_account_drops_the_source_plan(self, chart: Chart):
        book = chart.book
        book.set_budget(chart.fuel.id, YEAR)
        book.set_budget(chart.groceries.id, {"2026-01": "400.00"})
        book.execute(
            c.MergeAccounts(source_id=chart.fuel.id, target_id=chart.groceries.id)
        )
        assert book.budget_for(chart.groceries.id).months == {"2026-01": 40000}
        # The source is gone, so nothing references its plan.
        assert {b.account_id for b in book.budgets()} == {chart.groceries.id}

    def test_budgets_are_synced_like_an_entity(self, chart: Chart):
        from pymoney.core.changelog import ChangeOp, EntityKind

        book = chart.book
        before = book.revision
        budget = book.set_budget(chart.groceries.id, YEAR)
        changes = book.changes_since(before)
        assert any(
            ch.kind is EntityKind.BUDGET
            and ch.op is ChangeOp.CREATE
            and ch.entity_id == budget.id
            for ch in changes
        )

    def test_check_integrity_flags_a_plan_on_a_missing_account(self, chart: Chart):
        from pymoney.core.changelog import EntityKind

        book = chart.book
        budget = book.set_budget(chart.fuel.id, YEAR)
        assert book.check_integrity() == ()
        book.store.begin()
        book.store.delete(EntityKind.ACCOUNT, chart.fuel.id)
        book.store.commit()
        assert any("missing account" in p for p in book.check_integrity())
        assert budget.id  # keep the name referenced

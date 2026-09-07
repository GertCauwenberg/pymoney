"""Scheduled / recurring transactions: the recurrence maths, the Schedule
cursor, and the five commands.

The load-bearing promises: the k-th occurrence is a pure function of the rule
(no drift), the cursor can only move forward, posting an occurrence and
advancing the schedule is one revision, and two clients posting the same
occurrence converge instead of doubling it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from pymoney.core import (
    Frequency,
    Recurrence,
    Schedule,
    ScheduleTemplate,
    TemplateLeg,
)
from pymoney.core import commands as cmd
from pymoney.core.errors import ConflictError, NotFoundError, ValidationError
from pymoney.core.posting import LegSpec
from pymoney.core.recurrence import add_months
from tests.conftest import Chart

D = date


def _noon(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


class TestRecurrence:
    def test_monthly_clamps_the_day_and_never_drifts(self):
        r = Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 31))
        assert [r.nth(k) for k in range(4)] == [
            D(2026, 1, 31),
            D(2026, 2, 28),
            D(2026, 3, 31),  # back to the 31st -- measured from the original day
            D(2026, 4, 30),
        ]

    def test_add_months_leap_year(self):
        assert add_months(D(2024, 1, 31), 1) == D(2024, 2, 29)
        assert add_months(D(2025, 2, 28), 12) == D(2026, 2, 28)

    @pytest.mark.parametrize(
        ("freq", "interval", "expected"),
        [
            (Frequency.DAILY, 1, [D(2026, 1, 1), D(2026, 1, 2), D(2026, 1, 3)]),
            (Frequency.WEEKLY, 2, [D(2026, 1, 1), D(2026, 1, 15), D(2026, 1, 29)]),
            (Frequency.YEARLY, 1, [D(2026, 1, 1), D(2027, 1, 1), D(2028, 1, 1)]),
        ],
    )
    def test_frequencies(self, freq, interval, expected):
        r = Recurrence(frequency=freq, start=D(2026, 1, 1), interval=interval)
        assert [r.nth(k) for k in range(3)] == expected

    def test_monthly_by_weekday_is_the_nth_weekday(self):
        # 16 Jan 2026 is the third Friday.
        r = Recurrence(
            frequency=Frequency.MONTHLY, start=D(2026, 1, 16), monthly_by_weekday=True
        )
        assert [r.nth(k) for k in range(3)] == [
            D(2026, 1, 16),
            D(2026, 2, 20),
            D(2026, 3, 20),
        ]
        assert "third Friday" in r.describe()

    def test_count_and_until_bound_the_series(self):
        by_count = Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1), count=3)
        assert list(by_count.occurrences()) == [
            D(2026, 1, 1),
            D(2026, 2, 1),
            D(2026, 3, 1),
        ]
        by_date = Recurrence(
            frequency=Frequency.WEEKLY, start=D(2026, 1, 1), until=D(2026, 1, 20)
        )
        assert list(by_date.occurrences()) == [
            D(2026, 1, 1),
            D(2026, 1, 8),
            D(2026, 1, 15),
        ]
        assert by_count.bounded_count() == 3 and by_date.bounded_count() == 3

    def test_an_open_recurrence_has_no_total(self):
        assert (
            Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1)).bounded_count()
            is None
        )

    def test_refusals(self):
        with pytest.raises(ValidationError, match="interval"):
            Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1), interval=0)
        with pytest.raises(ValidationError, match="before it starts"):
            Recurrence(
                frequency=Frequency.DAILY, start=D(2026, 2, 1), until=D(2026, 1, 1)
            )
        with pytest.raises(ValidationError, match="monthly_by_weekday"):
            Recurrence(
                frequency=Frequency.WEEKLY, start=D(2026, 1, 1), monthly_by_weekday=True
            )

    def test_round_trips_through_a_dict(self):
        r = Recurrence(
            frequency=Frequency.MONTHLY,
            start=D(2026, 3, 31),
            interval=2,
            count=6,
            monthly_by_weekday=False,
        )
        assert Recurrence.from_dict(r.to_dict()) == r


def _template(chart: Chart, amount: str = "-1200.00") -> ScheduleTemplate:
    return ScheduleTemplate(
        legs=(
            TemplateLeg(account_id=chart.checking.id, value=amount),
            TemplateLeg(account_id=chart.fuel.id),
        ),
        memo="Monthly rent",
    )


class TestScheduleEntity:
    def test_the_cursor_advances_and_finishes(self, chart: Chart):
        r = Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1), count=3)
        s = Schedule(
            name="Rent", template=_template(chart), recurrence=r, book_id=chart.book.id
        )
        assert s.next_due == D(2026, 1, 1)
        assert s.pending(D(2026, 2, 15)) == (D(2026, 1, 1), D(2026, 2, 1))
        s = s.consumed(on=D(2026, 1, 1))
        assert s.occurrences_done == 1 and s.next_due == D(2026, 2, 1)
        assert s.last_occurrence == D(2026, 1, 1)
        s = s.consumed(on=D(2026, 2, 1)).consumed(on=D(2026, 3, 1))
        assert s.is_finished and s.next_due is None
        assert not s.is_due(D(2030, 1, 1))
        with pytest.raises(ValidationError, match="no occurrence left"):
            s.consumed(on=D(2026, 4, 1))

    def test_consuming_out_of_order_is_refused(self, chart: Chart):
        r = Recurrence(frequency=Frequency.WEEKLY, start=D(2026, 1, 5))
        s = Schedule(
            name="Gym", template=_template(chart), recurrence=r, book_id=chart.book.id
        )
        with pytest.raises(ValidationError, match="is 2026-01-05, not"):
            s.consumed(on=D(2026, 1, 12))

    def test_paused_is_never_due(self, chart: Chart):
        r = Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1))
        s = Schedule(
            name="Daily",
            template=_template(chart),
            recurrence=r,
            book_id=chart.book.id,
            paused=True,
        )
        assert not s.is_due(D(2026, 6, 1)) and s.pending(D(2026, 6, 1)) == ()

    def test_a_schedule_needs_a_name_and_parts(self, chart: Chart):
        r = Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1))
        with pytest.raises(ValidationError, match="needs a name"):
            Schedule(
                name="  ", template=_template(chart), recurrence=r, book_id=chart.book.id
            )
        with pytest.raises(ValidationError, match="needs a template"):
            Schedule(name="X", recurrence=r, book_id=chart.book.id)

    def test_a_template_allows_only_one_residual_leg(self, chart: Chart):
        with pytest.raises(ValidationError, match="one template leg"):
            ScheduleTemplate(
                legs=(
                    TemplateLeg(account_id=chart.checking.id),
                    TemplateLeg(account_id=chart.fuel.id),
                )
            )


class TestScheduleCommands:
    def test_create_post_and_advance_is_one_revision(self, chart: Chart):
        book = chart.book
        r = Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1))
        schedule = book.execute(
            cmd.CreateSchedule(name="Rent", template=_template(chart), recurrence=r)
        ).entity
        assert book.schedules() == (schedule,)

        chart.clock.set(_noon(D(2026, 3, 20)))
        due = book.due_schedules()
        assert [(s.name, len(dates)) for s, dates in due] == [("Rent", 3)]

        before = book.revision
        result = book.execute(cmd.PostScheduledOccurrence(schedule_id=schedule.id))
        assert book.revision == before + 1
        kinds = sorted(c.kind.value for c in result.changes)
        assert kinds == ["SCHEDULE", "TRANSACTION"]
        posted = result.entity
        assert posted.date == D(2026, 1, 1) and posted.memo == "Monthly rent"
        assert posted.imported_id == f"sched:{schedule.id}:2026-01-01"
        assert book.schedule(schedule.id).next_due == D(2026, 2, 1)
        assert book.schedule(schedule.id).last_occurrence == D(2026, 1, 1)
        assert book.balance(chart.checking.id) == book.balance(chart.checking.id)
        assert book.balance(chart.fuel.id).format(symbol=False) == "1200.00"

    def test_posting_the_same_occurrence_from_two_books_converges(self, chart: Chart):
        """Two Book objects over one store (a server and a replica), both told
        to post the January occurrence. The second finds it already there and
        advances its cursor to match -- no duplicate, no shared idempotency
        key."""
        from pymoney.core.book import Book

        server = chart.book
        schedule = server.execute(
            cmd.CreateSchedule(
                name="Netflix",
                template=_template(chart, "-12.99"),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        replica = Book(id=server.id, store=server.store, clock=server.clock)

        jan = D(2026, 1, 1)
        on_server = server.execute(
            cmd.PostScheduledOccurrence(schedule_id=schedule.id, on=jan)
        ).entity
        on_replica = replica.execute(
            cmd.PostScheduledOccurrence(schedule_id=schedule.id, on=jan)
        ).entity

        assert on_replica.id == on_server.id  # the same transaction, not a copy
        assert server.schedule(schedule.id).occurrences_done == 1
        assert replica.schedule(schedule.id).occurrences_done == 1
        assert server.balance(chart.fuel.id).format(symbol=False) == "12.99"  # once

    def test_skip_advances_without_posting(self, chart: Chart):
        book = chart.book
        chart.clock.set(_noon(D(2026, 3, 1)))
        schedule = book.execute(
            cmd.CreateSchedule(
                name="Rent",
                template=_template(chart),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        book.execute(cmd.SkipScheduledOccurrence(schedule_id=schedule.id))
        assert book.schedule(schedule.id).next_due == D(2026, 2, 1)
        assert book.transactions() == ()

    def test_an_occurrence_can_be_overridden_for_this_time_only(self, chart: Chart):
        book = chart.book
        chart.clock.set(_noon(D(2026, 1, 15)))
        schedule = book.execute(
            cmd.CreateSchedule(
                name="Salary",
                template=ScheduleTemplate(
                    legs=(
                        TemplateLeg(account_id=chart.checking.id, value="2500.00"),
                        TemplateLeg(account_id=chart.salary.id),
                    ),
                    estimate=True,
                ),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        txn = book.execute(
            cmd.PostScheduledOccurrence(
                schedule_id=schedule.id,
                legs=(
                    LegSpec.of(chart.checking.id, "2712.40"),
                    LegSpec.of(chart.salary.id),
                ),
                memo="January pay (with bonus)",
            )
        ).entity
        assert txn.memo == "January pay (with bonus)"
        assert book.balance(chart.checking.id).format(symbol=False) == "2712.40"
        # The template is untouched; next month is back to the estimate.
        assert book.schedule(schedule.id).template.legs[0].value == 2500

    def test_idempotency_key_does_not_double_advance(self, chart: Chart):
        book = chart.book
        chart.clock.set(_noon(D(2026, 2, 1)))
        schedule = book.execute(
            cmd.CreateSchedule(
                name="Rent",
                template=_template(chart),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        key = uuid4()
        a = book.execute(
            cmd.PostScheduledOccurrence(schedule_id=schedule.id, command_id=key)
        )
        b = book.execute(
            cmd.PostScheduledOccurrence(schedule_id=schedule.id, command_id=key)
        )
        assert b.replayed and a.entity.id == b.entity.id
        assert book.schedule(schedule.id).occurrences_done == 1

    def test_update_recomputes_the_cursor_and_validates(self, chart: Chart):
        book = chart.book
        schedule = book.execute(
            cmd.CreateSchedule(
                name="Rent",
                template=_template(chart),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        moved = book.execute(
            cmd.UpdateSchedule(
                schedule_id=schedule.id,
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 6, 5)),
                name="Rent (new flat)",
            )
        ).entity
        assert moved.next_due == D(2026, 6, 5) and moved.name == "Rent (new flat)"

        gone_account = uuid4()
        with pytest.raises(NotFoundError):
            book.execute(
                cmd.UpdateSchedule(
                    schedule_id=schedule.id,
                    template=ScheduleTemplate(
                        legs=(TemplateLeg(account_id=gone_account),)
                    ),
                )
            )

    def test_pausing_and_deleting(self, chart: Chart):
        book = chart.book
        chart.clock.set(_noon(D(2026, 3, 1)))
        schedule = book.execute(
            cmd.CreateSchedule(
                name="Gym",
                template=_template(chart, "-35.00"),
                recurrence=Recurrence(frequency=Frequency.MONTHLY, start=D(2026, 1, 1)),
            )
        ).entity
        book.execute(cmd.UpdateSchedule(schedule_id=schedule.id, paused=True))
        assert book.due_schedules() == ()
        assert book.schedules(include_paused=False) == ()
        book.execute(cmd.DeleteSchedule(schedule_id=schedule.id))
        with pytest.raises(NotFoundError):
            book.schedule(schedule.id)

    def test_creating_with_a_missing_template_account_is_refused(self, chart: Chart):
        with pytest.raises(NotFoundError):
            chart.book.execute(
                cmd.CreateSchedule(
                    name="Bad",
                    template=ScheduleTemplate(legs=(TemplateLeg(account_id=uuid4()),)),
                    recurrence=Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1)),
                )
            )

    def test_a_duplicate_schedule_id_is_a_conflict(self, chart: Chart):
        book = chart.book
        sid = uuid4()
        book.execute(
            cmd.CreateSchedule(
                id=sid,
                name="A",
                template=_template(chart),
                recurrence=Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1)),
            )
        )
        with pytest.raises(ConflictError):
            book.execute(
                cmd.CreateSchedule(
                    id=sid,
                    name="B",
                    template=_template(chart),
                    recurrence=Recurrence(frequency=Frequency.DAILY, start=D(2026, 1, 1)),
                )
            )

"""Scheduled / recurring transactions, and the "due" panel.

Nothing here posts on a timer. ``GET .../schedules/due`` is what a client
calls to find out that three transactions are due; ``POST
.../schedules/{id}/post`` is what it calls, once per occurrence, when the user
confirms -- an ordinary idempotent command. ``auto_post`` only tells the
client it may confirm on the user's behalf; the server never does.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ...core import commands as cmd
from ...core.amount import Amount
from ...core.commands import UNSET
from ...core.posting import LegSpec
from ..deps import IdempotencyKey, ReadableBook, WritableBook
from ..schemas import (
    DuePanelOut,
    DueScheduleOut,
    PostOccurrenceIn,
    ScheduleIn,
    ScheduleOut,
    SchedulePatch,
    SkipOccurrenceIn,
    TransactionOut,
    to_fraction,
)
from ._shared import set_revision

router = APIRouter(prefix="/books/{book_id}/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut], summary="List schedules")
def list_schedules(
    book: ReadableBook,
    response: Response,
    include_finished: bool = Query(default=True),
    include_paused: bool = Query(default=True),
) -> list[ScheduleOut]:
    set_revision(response, book)
    return [
        ScheduleOut.of(s)
        for s in book.schedules(
            include_finished=include_finished, include_paused=include_paused
        )
    ]


@router.get(
    "/due",
    response_model=DuePanelOut,
    summary="Schedules with an occurrence due on or before a date",
)
def due_panel(
    book: ReadableBook,
    response: Response,
    on: date | None = Query(default=None, description="Defaults to the book's today."),
) -> DuePanelOut:
    """One occurrence's amount is estimated from the template's first
    non-residual leg, in the transaction currency -- a quick total for the
    panel, ``null`` when it cannot be known without posting."""
    set_revision(response, book)
    when = on or book.clock.today()
    rows: list[DueScheduleOut] = []
    total = 0
    for schedule, pending in book.due_schedules(when):
        total += len(pending)
        rows.append(DueScheduleOut.of(schedule, pending, _one_amount(book, schedule)))
    return DuePanelOut(as_of=when, due=rows, occurrences=total)


def _one_amount(book: ReadableBook, schedule) -> Amount | None:
    template = schedule.template
    if template is None or template.estimate:
        return None
    currency = (
        book.commodity(template.currency_id)
        if template.currency_id is not None
        else (book.base_currency if book.base_currency_id else None)
    )
    if currency is None:
        return None
    for leg in template.legs:
        if leg.value is not None:
            return Amount.from_major(currency, abs(leg.value))
    return None


@router.post(
    "",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule",
)
def create_schedule(
    body: ScheduleIn, book: WritableBook, response: Response, key: IdempotencyKey
) -> ScheduleOut:
    result = book.execute(
        cmd.CreateSchedule(
            name=body.name,
            template=body.template.to_domain(),
            recurrence=body.recurrence.to_domain(),
            auto_post=body.auto_post,
            paused=body.paused,
            id=body.id,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return ScheduleOut.of(result.entity)


@router.get("/{schedule_id}", response_model=ScheduleOut, summary="One schedule")
def get_schedule(
    schedule_id: UUID, book: ReadableBook, response: Response
) -> ScheduleOut:
    set_revision(response, book)
    return ScheduleOut.of(book.schedule(schedule_id))


@router.patch("/{schedule_id}", response_model=ScheduleOut, summary="Update a schedule")
def update_schedule(
    schedule_id: UUID,
    body: SchedulePatch,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> ScheduleOut:
    fields = body.model_dump(exclude_unset=True)
    result = book.execute(
        cmd.UpdateSchedule(
            schedule_id=schedule_id,
            name=fields.get("name", UNSET),
            template=(body.template.to_domain() if body.template is not None else UNSET),
            recurrence=(
                body.recurrence.to_domain() if body.recurrence is not None else UNSET
            ),
            auto_post=fields.get("auto_post", UNSET),
            paused=fields.get("paused", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return ScheduleOut.of(result.entity)


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
def delete_schedule(schedule_id: UUID, book: WritableBook, key: IdempotencyKey) -> None:
    book.execute(cmd.DeleteSchedule(schedule_id=schedule_id, command_id=key))


@router.post(
    "/{schedule_id}/post",
    response_model=TransactionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Post the occurrence this schedule has due",
)
def post_occurrence(
    schedule_id: UUID,
    body: PostOccurrenceIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> TransactionOut:
    """Posts the due occurrence and advances the schedule in one revision.
    ``legs`` replaces the template's legs for this occurrence only; the
    template stays as it was. A second client posting the same occurrence
    gets back the transaction that already exists."""
    fields = body.model_dump(exclude_unset=True)
    legs: object = UNSET
    if body.legs is not None:
        legs = tuple(
            LegSpec(
                account_id=leg.account_id,
                value=None if leg.value is None else to_fraction(leg.value),
                quantity=None if leg.quantity is None else to_fraction(leg.quantity),
                price=None if leg.price is None else to_fraction(leg.price),
                memo=leg.memo,
                payee_id=leg.payee_id,
                action=leg.action,
                number=leg.number,
                reconcile_state=leg.reconcile_state.to_domain(),
                reconcile_date=leg.reconcile_date,
            )
            for leg in body.legs
        )
    result = book.execute(
        cmd.PostScheduledOccurrence(
            schedule_id=schedule_id,
            on=fields.get("on", UNSET),
            legs=legs,  # type: ignore[arg-type]
            memo=fields.get("memo", UNSET),
            payee_id=fields.get("payee_id", UNSET),
            number=fields.get("number", UNSET),
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return TransactionOut.of(result.entity)


@router.post(
    "/{schedule_id}/skip",
    response_model=ScheduleOut,
    summary="Advance past the occurrence this schedule has due, without posting",
)
def skip_occurrence(
    schedule_id: UUID,
    body: SkipOccurrenceIn,
    book: WritableBook,
    response: Response,
    key: IdempotencyKey,
) -> ScheduleOut:
    result = book.execute(
        cmd.SkipScheduledOccurrence(
            schedule_id=schedule_id,
            on=UNSET if body.on is None else body.on,
            command_id=key,
        )
    )
    set_revision(response, book, result)
    return ScheduleOut.of(result.entity)


__all__ = ["router"]

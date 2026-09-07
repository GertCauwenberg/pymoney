"""Command handlers for scheduled / recurring transactions.

Nothing here posts on a timer. :func:`post_scheduled_occurrence` is the one
that writes a transaction, and it is always called by a client that has just
seen the schedule is due -- so the concurrency story is the ordinary one (a
command, a revision, an idempotency key), and it works the same online, on a
replica, or replayed from an outbox.

Posting an occurrence and advancing its schedule is **one command, one
revision**: the transaction and the cursor move together or not at all. The
posted transaction is stamped ``imported_id = sched:<schedule id>:<date>``, so
a second client that posts the same occurrence finds it already there and
converges on it instead of creating a duplicate.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

from .. import commands as cmd
from ..changelog import EntityKind
from ..errors import ConflictError, NotFoundError, ValidationError
from ..ids import new_id
from ..posting import LegSpec
from ..schedule import Schedule
from ..transaction import Transaction
from .transactions import build_splits

if TYPE_CHECKING:  # pragma: no cover
    from ..book import Book

SCHEDULE_DEDUP_PREFIX = "sched:"


def _validate_template(book: Book, schedule: Schedule) -> None:
    """The template's accounts and payees must exist; catch it at create/edit
    time, not on the first post."""
    template = schedule.template
    assert template is not None
    if template.currency_id is not None:
        book.commodity(template.currency_id)
    if template.payee_id is not None:
        book.payee(template.payee_id)
    if template.imbalance_account_id is not None:
        book.account(template.imbalance_account_id)
    for leg in template.legs:
        book.account(leg.account_id)
        if leg.payee_id is not None:
            book.payee(leg.payee_id)


def create_schedule(book: Book, command: cmd.CreateSchedule) -> Schedule:
    schedule = Schedule(
        id=command.id or new_id(),
        book_id=book.id,
        name=command.name,
        template=command.template,
        recurrence=command.recurrence,
        auto_post=command.auto_post,
        paused=command.paused,
        occurrences_done=command.occurrences_done,
    )
    _validate_template(book, schedule)
    if book.store.exists(EntityKind.SCHEDULE, schedule.id):
        raise ConflictError(f"schedule {schedule.id} already exists")
    return book.put(EntityKind.SCHEDULE, schedule, created=True)


def update_schedule(book: Book, command: cmd.UpdateSchedule) -> Schedule:
    current = book.schedule(command.schedule_id)
    updated = current
    for field_name in ("name", "template", "recurrence", "auto_post", "paused"):
        value = getattr(command, field_name)
        if not isinstance(value, cmd.Unset):
            # replace() reruns __post_init__, so next_due is recomputed if the
            # recurrence changed.
            updated = replace(updated, **{field_name: value})
    if updated == current:
        return current
    _validate_template(book, updated)
    return book.put(EntityKind.SCHEDULE, updated)


def delete_schedule(book: Book, command: cmd.DeleteSchedule) -> Schedule:
    schedule = book.schedule(command.schedule_id)
    return book.drop(EntityKind.SCHEDULE, schedule.id)


def _dedup_key(schedule: Schedule, occurrence: date) -> str:
    return f"{SCHEDULE_DEDUP_PREFIX}{schedule.id}:{occurrence.isoformat()}"


def skip_scheduled_occurrence(
    book: Book, command: cmd.SkipScheduledOccurrence
) -> Schedule:
    schedule = book.schedule(command.schedule_id)
    due = schedule.occurrence_due()
    if due is None:
        raise ValidationError(f"schedule {schedule.name!r} has nothing left to skip")
    if not isinstance(command.on, cmd.Unset) and command.on != due:
        raise ValidationError(
            f"the occurrence due for {schedule.name!r} is {due}, not {command.on}"
        )
    return book.put(
        EntityKind.SCHEDULE,
        schedule.consumed(on=due),
        note=f"skipped occurrence {due}",
    )


def post_scheduled_occurrence(
    book: Book, command: cmd.PostScheduledOccurrence
) -> Transaction:
    schedule = book.schedule(command.schedule_id)
    template = schedule.template
    assert template is not None and schedule.recurrence is not None

    due = schedule.occurrence_due()
    target = due if isinstance(command.on, cmd.Unset) else command.on

    # Convergence: if a transaction for this occurrence already exists (another
    # client, or this command replayed against a book that has moved on), do
    # not post again. Advance the cursor to sit just past it, and return what
    # is there. This is what keeps two spouses confirming the rent from
    # booking it twice, with no shared idempotency key.
    if target is not None:
        existing_id = book.store.find_imported(_dedup_key(schedule, target))
        if existing_id is not None:
            if due is not None and target == due:
                book.put(
                    EntityKind.SCHEDULE,
                    schedule.consumed(on=due),
                    note=f"occurrence {due} was already posted as {existing_id}",
                )
            return book.transaction(existing_id)

    if schedule.paused:
        raise ValidationError(f"schedule {schedule.name!r} is paused")
    if due is None:
        raise ValidationError(
            f"schedule {schedule.name!r} has no occurrence left to post"
        )
    if target != due:
        raise ValidationError(
            f"the occurrence due for {schedule.name!r} is {due}, not {target}"
        )
    occurrence = due
    dedup = _dedup_key(schedule, occurrence)

    currency = (
        book.commodity(template.currency_id)
        if template.currency_id is not None
        else book.base_currency
    )
    legs: tuple[LegSpec, ...]
    if not isinstance(command.legs, cmd.Unset):
        legs = tuple(command.legs)
    else:
        legs = template.legspecs()
    memo = template.memo if isinstance(command.memo, cmd.Unset) else command.memo
    payee_id = (
        template.payee_id if isinstance(command.payee_id, cmd.Unset) else command.payee_id
    )
    number = template.number if isinstance(command.number, cmd.Unset) else command.number
    if payee_id is not None:
        book.payee(payee_id)

    splits = build_splits(
        book, legs, currency, template.rounding, template.imbalance_account_id
    )
    for split in splits:
        if split.payee_id is not None:
            book.payee(split.payee_id)
    transaction = Transaction(
        id=command.transaction_id or new_id(),
        book_id=book.id,
        date=occurrence,
        currency=currency,
        splits=splits,
        memo=memo,
        payee_id=payee_id,
        number=number,
        rounding=template.rounding,
        entered_at=book.clock.now(),
        imported_id=dedup,
    )
    transaction.validate()
    posted = book.put(
        EntityKind.TRANSACTION,
        transaction,
        created=True,
        note=f"scheduled: {schedule.name}",
    )
    book.put(
        EntityKind.SCHEDULE,
        schedule.consumed(on=occurrence),
        note=f"posted occurrence {occurrence}",
    )
    return posted


# NotFoundError / ConflictError are imported for the handlers above and
# re-exported so the router's error mapping can rely on them here.
__all__ = [
    "SCHEDULE_DEDUP_PREFIX",
    "ConflictError",
    "NotFoundError",
    "create_schedule",
    "delete_schedule",
    "post_scheduled_occurrence",
    "skip_scheduled_occurrence",
    "update_schedule",
]

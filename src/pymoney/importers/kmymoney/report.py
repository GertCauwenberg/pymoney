"""What the import did, and what it could not do.

An importer that silently drops data is worse than one that refuses to run, so
this is not a log: it is a structured account of every decision, every
approximation and every feature that did not survive the crossing. A user
migrating ten years of records deserves to know exactly what changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from ...core.amount import Amount

__all__ = ["Adjustment", "ImportReport", "Level", "Problem"]


class Level(StrEnum):
    INFO = "INFO"
    """Something was mapped imperfectly but faithfully."""
    WARNING = "WARNING"
    """Data was changed or dropped; the user should look."""
    ERROR = "ERROR"
    """The import is not trustworthy."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Problem:
    level: Level
    category: str
    message: str
    source_id: str = ""
    """The KMyMoney id, so the user can find it in their own file."""

    def __str__(self) -> str:
        where = f" [{self.source_id}]" if self.source_id else ""
        return f"{self.level.value:<7} {self.category}{where}: {self.message}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Adjustment:
    """A rounding correction posted to make one account's balance come out right.

    KMyMoney keeps split amounts as unbounded rationals; we keep them as integer
    minor units, because you cannot pay a third of a cent. Summing rounded
    amounts is not the same as rounding a summed amount, so an account can end up
    a cent or two away from the balance KMyMoney displays. This records the
    difference and the transaction posted to close it.
    """

    account_path: str
    commodity_code: str
    kmymoney_exact: Fraction
    """KMyMoney's balance, as the exact rational it keeps internally."""
    kmymoney_booked: Amount
    """That balance rounded to the smallest unit -- what KMyMoney displays."""
    imported: Amount
    """What summing the individually-rounded splits produced."""
    delta: Amount
    """What had to be posted. Always small; usually one or two minor units."""
    split_count: int
    transaction_id: UUID | None = None

    def __str__(self) -> str:
        return (
            f"{self.account_path}: imported {self.imported.format(symbol=False)}, "
            f"KMyMoney shows {self.kmymoney_booked.format(symbol=False)}, "
            f"adjusted by {self.delta.format(symbol=False)} "
            f"{self.commodity_code} over {self.split_count} split(s)"
        )


@dataclass(slots=True, kw_only=True)
class ImportReport:
    """The outcome of one import."""

    source: Path
    book_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    imported: dict[str, int] = field(default_factory=dict)
    """How many of each thing was created."""
    skipped: dict[str, int] = field(default_factory=dict)
    """Features in the file that PyMoney has no home for yet."""
    problems: list[Problem] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    revision_before: int = 0
    revision_after: int = 0
    dry_run: bool = False

    # -- recording ---------------------------------------------------------
    def note(
        self,
        category: str,
        message: str,
        *,
        source_id: str = "",
        level: Level = Level.INFO,
    ) -> Problem:
        problem = Problem(
            level=level, category=category, message=message, source_id=source_id
        )
        self.problems.append(problem)
        return problem

    def warn(self, category: str, message: str, *, source_id: str = "") -> Problem:
        return self.note(category, message, source_id=source_id, level=Level.WARNING)

    def error(self, category: str, message: str, *, source_id: str = "") -> Problem:
        return self.note(category, message, source_id=source_id, level=Level.ERROR)

    def count(self, what: str, amount: int = 1) -> None:
        self.imported[what] = self.imported.get(what, 0) + amount

    def skip(self, what: str, amount: int) -> None:
        if amount > 0:
            self.skipped[what] = self.skipped.get(what, 0) + amount

    # -- querying ----------------------------------------------------------
    def of_level(self, level: Level) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.level is level)

    @property
    def errors(self) -> tuple[Problem, ...]:
        return self.of_level(Level.ERROR)

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return self.of_level(Level.WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing went wrong that the user must act on."""
        return not self.errors

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def total_rounding_delta(self) -> int:
        """Total absolute rounding correction, in minor units."""
        return sum(abs(a.delta.minor) for a in self.adjustments)

    # -- presentation ------------------------------------------------------
    def summary(self, *, verbose: bool = False) -> str:
        lines: list[str] = []
        head = "Dry run of" if self.dry_run else "Imported"
        lines.append(f"{head} {self.source}")
        if self.duration_seconds is not None:
            lines.append(f"  took {self.duration_seconds:.1f}s")
        if not self.dry_run:
            lines.append(f"  revisions r{self.revision_before} -> r{self.revision_after}")

        if self.imported:
            lines.append("")
            lines.append("  imported:")
            for name, number in sorted(self.imported.items()):
                lines.append(f"    {number:>9,}  {name}")

        if self.skipped:
            lines.append("")
            lines.append("  not imported (no equivalent in PyMoney yet):")
            for name, number in sorted(self.skipped.items()):
                lines.append(f"    {number:>9,}  {name}")

        if self.adjustments:
            lines.append("")
            lines.append(
                f"  rounding adjustments: {len(self.adjustments)} account(s), "
                f"{self.total_rounding_delta} minor unit(s) in total"
            )
            lines.append(
                "    KMyMoney stores split amounts as exact fractions; PyMoney "
                "books whole"
            )
            lines.append(
                "    minor units. Where that differs, a correcting entry was "
                "posted so that"
            )
            lines.append("    every balance matches what KMyMoney displays.")
            shown = self.adjustments if verbose else self.adjustments[:10]
            for adjustment in shown:
                lines.append(f"      {adjustment}")
            if len(shown) < len(self.adjustments):
                lines.append(
                    f"      ... and {len(self.adjustments) - len(shown)} more "
                    f"(use --verbose)"
                )

        for level in (Level.ERROR, Level.WARNING, Level.INFO):
            selected = self.of_level(level)
            if not selected:
                continue
            if level is Level.INFO and not verbose:
                lines.append("")
                lines.append(f"  {len(selected)} note(s); use --verbose to see them")
                continue
            lines.append("")
            lines.append(f"  {level.value}:")
            visible = selected if verbose else selected[:20]
            for problem in visible:
                lines.append(f"    {problem}")
            if len(visible) < len(selected):
                lines.append(f"    ... and {len(selected) - len(visible)} more")

        lines.append("")
        lines.append("  result: " + ("OK" if self.ok else "FAILED"))
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()

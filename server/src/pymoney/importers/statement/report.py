"""What a statement import did -- posted, skipped, and could not post.

Smaller than the KMyMoney report (a weekly statement is not a ten-year
migration) and it reuses that report's :class:`~pymoney.importers.kmymoney.Level`
and :class:`~pymoney.importers.kmymoney.Problem` vocabulary, so a client that
renders one renders the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from ..kmymoney.report import Level, Problem

__all__ = ["Level", "Problem", "StatementReport"]


@dataclass(slots=True, kw_only=True)
class StatementReport:
    """The outcome of importing one statement into one account."""

    account_id: UUID
    account_path: str = ""
    rows: int = 0
    """Lines read from the file."""
    posted: int = 0
    """New transactions created."""
    skipped_duplicates: int = 0
    """Rows already in the book (same ``imported_id``), left alone."""
    skipped_transfers: int = 0
    """Rows whose far side was already recorded in another account."""
    skipped_by_user: int = 0
    """Rows the reviewer chose not to import."""
    payees_created: int = 0
    uncategorised: int = 0
    """Posted rows with nothing but the residual (to ``imbalance`` or a chosen
    fallback); the register shows them, so the household can finish the job."""
    revision_before: int = 0
    revision_after: int = 0
    posted_ids: list[UUID] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    # -- recording ---------------------------------------------------------
    def note(
        self,
        category: str,
        message: str,
        *,
        level: Level = Level.INFO,
        source_id: str = "",
    ) -> Problem:
        problem = Problem(
            level=level, category=category, message=message, source_id=source_id
        )
        self.problems.append(problem)
        return problem

    def warn(self, category: str, message: str, *, source_id: str = "") -> Problem:
        return self.note(category, message, level=Level.WARNING, source_id=source_id)

    def error(self, category: str, message: str, *, source_id: str = "") -> Problem:
        return self.note(category, message, level=Level.ERROR, source_id=source_id)

    # -- querying ---------------------------------------------------------
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
        return not self.errors

    @property
    def skipped(self) -> int:
        return self.skipped_duplicates + self.skipped_transfers + self.skipped_by_user

    # -- presentation ----------------------------------------------------
    def summary(self) -> str:
        where = self.account_path or str(self.account_id)
        head = f"{self.posted} transaction(s) imported into {where}"
        if self.payees_created:
            head += f", {self.payees_created} payee(s) created"
        bits: list[str] = [head]
        if self.skipped_duplicates:
            bits.append(f"{self.skipped_duplicates} already imported (skipped)")
        if self.skipped_transfers:
            bits.append(
                f"{self.skipped_transfers} already recorded from the other "
                f"account (skipped)"
            )
        if self.skipped_by_user:
            bits.append(f"{self.skipped_by_user} skipped on review")
        if self.uncategorised:
            bits.append(
                f"{self.uncategorised} posted without a category -- they are in "
                f"the register waiting for one"
            )
        if self.errors:
            bits.append(f"{len(self.errors)} could not be posted")
        return "; ".join(bits) + "."

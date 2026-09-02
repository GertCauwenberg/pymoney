"""Payees (decision #13: merges are first-class operations).

Payees accumulate typos and duplicates faster than any other entity in a
personal finance app ("Amazon", "AMAZON EU", "Amazon.de").  Merging them must
therefore be a real command that lands in the changelog with one revision, not a
bulk UPDATE that clients cannot sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .entity import Entity
from .errors import ValidationError

__all__ = ["Payee"]


@dataclass(frozen=True, slots=True, kw_only=True)
class Payee(Entity):
    """Someone you pay or get paid by."""

    name: str = ""
    notes: str = ""
    address: str = ""
    match_keys: tuple[str, ...] = ()
    """Alternative spellings absorbed by merges, used by import matching."""
    default_account_id: UUID | None = None
    """Category suggested when this payee appears in an import."""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValidationError("payee name must not be empty")

    @property
    def key(self) -> str:
        return self.name.strip().casefold()

    def with_match_keys(self, extra: tuple[str, ...]) -> Payee:
        from dataclasses import replace

        merged = list(self.match_keys)
        for item in extra:
            folded = item.strip()
            if folded and folded.casefold() != self.key and folded not in merged:
                merged.append(folded)
        return replace(self, match_keys=tuple(merged))

    def __str__(self) -> str:
        return self.name

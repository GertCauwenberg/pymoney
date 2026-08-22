"""Column types that behave identically on SQLite and PostgreSQL.

PostgreSQL has native ``uuid`` and ``jsonb``; SQLite has neither. Rather than let
the domain see the difference, each type below has a dialect-specific
implementation and one Python-side representation.

``Uuid``
    ``uuid`` on PostgreSQL, 16 raw bytes on SQLite. Bytes rather than a 36-char
    string because UUIDv7 ids are *most* of the data in the ``splits`` and
    ``changes`` tables, and 16 bytes beats 36 both in storage and in index depth.
    Byte order preserves the v7 timestamp prefix, so index locality survives.

``JsonList``
    ``jsonb`` on PostgreSQL, JSON text on SQLite, a ``tuple[str, ...]`` in
    Python. Used for a payee's match keys, which are a genuinely unordered
    little set, not a relation worth a table.

``UtcDateTime``
    ``timestamptz`` on PostgreSQL. SQLite has no concept of a timezone and hands
    back a naive datetime, which would break every audit stamp and every
    ``Price.as_of`` (the domain requires those to be aware). So this type
    normalises to UTC on the way in and re-attaches UTC on the way out.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Dialect, LargeBinary, Text, TypeDecorator
from sqlalchemy.dialects import postgresql

__all__ = ["JsonList", "UtcDateTime", "Uuid", "fits_in_bigint"]

_BIGINT_MAX = 2**63 - 1


def fits_in_bigint(value: int) -> bool:
    """True if an integer survives a round trip through BIGINT.

    Rates are stored as two BIGINTs. A quote provider will never produce a
    numerator that big, but a hand-entered or generated rational could, and
    silently truncating one would corrupt the ledger.
    """
    return -_BIGINT_MAX - 1 <= value <= _BIGINT_MAX


class Uuid(TypeDecorator):
    """A UUID: native on PostgreSQL, 16 bytes on SQLite."""

    impl = LargeBinary(16)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(LargeBinary(16))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, UUID):
            raise TypeError(f"expected a UUID, got {type(value).__name__}: {value!r}")
        if dialect.name == "postgresql":
            return value
        return value.bytes

    def process_result_value(self, value: Any, dialect: Dialect) -> UUID | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return value
        return UUID(bytes=bytes(value))

    def __repr__(self) -> str:  # pragma: no cover - Alembic renders this
        return "Uuid()"


class JsonList(TypeDecorator):
    """A tuple of strings, as ``jsonb`` or JSON text."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        items = list(value or ())
        if dialect.name == "postgresql":
            return items
        return json.dumps(items)

    def process_result_value(self, value: Any, dialect: Dialect) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(json.loads(value))
        return tuple(value)

    def __repr__(self) -> str:  # pragma: no cover - Alembic renders this
        return "JsonList()"


class UtcDateTime(TypeDecorator):
    """A timezone-aware instant, stored as UTC and returned as UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                f"refusing to store a naive datetime: {value!r}. Audit stamps are "
                f"UTC instants (decision #10)."
            )
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # SQLite: the column has no timezone, but we only ever store UTC.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def __repr__(self) -> str:  # pragma: no cover - Alembic renders this
        return "UtcDateTime()"

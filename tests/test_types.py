"""Portable column types, tested against both dialects directly.

These go through the ``TypeDecorator`` API rather than a live database, which is
the only way to exercise the PostgreSQL *and* SQLite branches in one run. The
integration tests in ``test_store.py`` then confirm the same behaviour end to
end on a real server.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from pymoney.core import new_id
from pymoney.store.types import JsonList, UtcDateTime, Uuid, fits_in_bigint

SQLITE = sqlite.dialect()
POSTGRES = postgresql.dialect()
DIALECTS = [pytest.param(SQLITE, id="sqlite"), pytest.param(POSTGRES, id="postgresql")]


@pytest.fixture(params=DIALECTS)
def dialect(request: pytest.FixtureRequest):
    return request.param


class TestUuid:
    def test_round_trip(self, dialect):
        value = new_id()
        column = Uuid()
        stored = column.process_bind_param(value, dialect)
        assert column.process_result_value(stored, dialect) == value

    def test_none_round_trips(self, dialect):
        column = Uuid()
        assert column.process_bind_param(None, dialect) is None
        assert column.process_result_value(None, dialect) is None

    def test_sqlite_uses_sixteen_bytes(self):
        """36-char strings would nearly double the size of the biggest indexes."""
        stored = Uuid().process_bind_param(new_id(), SQLITE)
        assert isinstance(stored, bytes)
        assert len(stored) == 16

    def test_postgresql_keeps_the_native_type(self):
        value = new_id()
        assert Uuid().process_bind_param(value, POSTGRES) is value

    def test_byte_order_preserves_time_ordering(self):
        """UUIDv7's point is sortable keys; the encoding must not destroy that."""
        column = Uuid()
        ids = [new_id() for _ in range(50)]
        encoded = [column.process_bind_param(i, SQLITE) for i in ids]
        assert encoded == sorted(encoded)

    def test_a_non_uuid_is_refused(self, dialect):
        with pytest.raises(TypeError, match="expected a UUID"):
            Uuid().process_bind_param("01a0-not-a-uuid", dialect)
        with pytest.raises(TypeError, match="expected a UUID"):
            Uuid().process_bind_param(12345, dialect)

    def test_an_already_decoded_uuid_passes_through(self, dialect):
        value = new_id()
        assert Uuid().process_result_value(value, dialect) == value

    def test_memoryview_results_are_accepted(self):
        """Some drivers hand back a memoryview rather than bytes."""
        value = new_id()
        assert Uuid().process_result_value(memoryview(value.bytes), SQLITE) == value

    def test_dialect_implementations_differ(self):
        column = Uuid()
        assert "UUID" in repr(column.load_dialect_impl(POSTGRES)).upper()
        assert "LARGEBINARY" in repr(column.load_dialect_impl(SQLITE)).upper()
        assert repr(column) == "Uuid()"


class TestJsonList:
    def test_round_trip(self, dialect):
        column = JsonList()
        value = ("AMZN", "Amazon EU")
        stored = column.process_bind_param(value, dialect)
        assert column.process_result_value(stored, dialect) == value

    def test_empty(self, dialect):
        column = JsonList()
        assert column.process_result_value(column.process_bind_param((), dialect), dialect) == ()

    def test_none_reads_as_empty(self, dialect):
        """A payee with no match keys and one with an empty list are the same."""
        assert JsonList().process_result_value(None, dialect) == ()
        assert JsonList().process_bind_param(None, SQLITE) == "[]"

    def test_sqlite_stores_json_text(self):
        stored = JsonList().process_bind_param(("a", "b"), SQLITE)
        assert isinstance(stored, str)
        assert json.loads(stored) == ["a", "b"]

    def test_postgresql_stores_a_list_for_jsonb(self):
        assert JsonList().process_bind_param(("a", "b"), POSTGRES) == ["a", "b"]

    def test_result_is_always_a_tuple(self, dialect):
        """Immutable in, immutable out: the domain field is a tuple."""
        assert isinstance(JsonList().process_result_value(["a"], dialect), tuple)

    def test_dialect_implementations_differ(self):
        column = JsonList()
        assert "JSONB" in repr(column.load_dialect_impl(POSTGRES)).upper()
        assert "TEXT" in repr(column.load_dialect_impl(SQLITE)).upper()
        assert repr(column) == "JsonList()"


class TestUtcDateTime:
    def test_round_trip(self, dialect):
        column = UtcDateTime()
        value = datetime(2026, 1, 31, 17, 30, tzinfo=timezone.utc)
        stored = column.process_bind_param(value, dialect)
        assert column.process_result_value(stored, dialect) == value

    def test_none_round_trips(self, dialect):
        column = UtcDateTime()
        assert column.process_bind_param(None, dialect) is None
        assert column.process_result_value(None, dialect) is None

    def test_other_offsets_are_normalised_to_utc(self, dialect):
        column = UtcDateTime()
        india = timezone(timedelta(hours=5, minutes=30))
        stored = column.process_bind_param(
            datetime(2026, 1, 31, 5, 30, tzinfo=india), dialect
        )
        assert stored == datetime(2026, 1, 31, tzinfo=timezone.utc)

    def test_a_naive_datetime_is_refused(self, dialect):
        """Audit stamps are UTC instants (decision #10); naive means unknown."""
        with pytest.raises(ValueError, match="naive datetime"):
            UtcDateTime().process_bind_param(datetime(2026, 1, 1), dialect)

    def test_sqlite_results_get_utc_reattached(self):
        """SQLite has no timezone type, so it hands back a naive value."""
        result = UtcDateTime().process_result_value(datetime(2026, 1, 1), SQLITE)
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert result.tzinfo is not None

    def test_repr(self):
        assert repr(UtcDateTime()) == "UtcDateTime()"


class TestBigintGuard:
    @pytest.mark.parametrize(
        ("value", "fits"),
        [
            (0, True),
            (2**63 - 1, True),
            (-(2**63), True),
            (2**63, False),
            (-(2**63) - 1, False),
            (10**30, False),
        ],
    )
    def test_range(self, value, fits):
        assert fits_in_bigint(value) is fits


def test_uuid_decoding_accepts_the_exact_bytes():
    value = UUID("01a014dc-ccbf-7000-a7de-e0d84e73cc29")
    assert Uuid().process_result_value(value.bytes, SQLITE) == value

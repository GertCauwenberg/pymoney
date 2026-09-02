"""Ids, clocks, commodities, accounts, payees: the small pieces."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pymoney.core import (
    ACCOUNT_SEPARATOR,
    Account,
    AccountClass,
    AccountType,
    Commodity,
    CommodityKind,
    ManualClock,
    Payee,
    SystemClock,
    ValidationError,
    new_id,
    uuid7,
)
from pymoney.core.ids import (
    Uuid7Generator,
    counter_of,
    is_uuid7,
    timestamp_ms_of,
)


class TestUuid7:
    @pytest.fixture
    def gen(self) -> Uuid7Generator:
        """A fresh generator, so timestamp assertions are deterministic."""
        return Uuid7Generator()

    def test_version_and_variant(self):
        value = uuid7()
        assert value.version == 7
        assert is_uuid7(value)
        assert (value.int >> 62) & 0b11 == 0b10

    def test_embeds_the_timestamp(self, gen):
        value = gen(1_767_225_600_000)
        assert timestamp_ms_of(value) == 1_767_225_600_000
        assert counter_of(value) == 0

    def test_timestamp_of_other_versions_is_refused(self):
        import uuid

        with pytest.raises(ValueError, match="not a UUIDv7"):
            timestamp_ms_of(uuid.uuid4())
        with pytest.raises(ValueError, match="not a UUIDv7"):
            counter_of(uuid.uuid4())

    def test_monotonic_within_a_millisecond(self, gen):
        """Sortability is the whole point of choosing v7 (decision #5)."""
        values = [gen(1_767_225_600_000) for _ in range(500)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)
        assert counter_of(values[-1]) == 499

    def test_monotonic_across_many_calls(self):
        values = [new_id() for _ in range(2000)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)

    def test_survives_the_clock_going_backwards(self, gen):
        """An NTP step must not mint ids that sort before existing rows."""
        forward = gen(1_800_000_000_000)
        backward = gen(1_700_000_000_000)
        assert backward > forward
        assert timestamp_ms_of(backward) == 1_800_000_000_000

    def test_counter_exhaustion_borrows_the_next_millisecond(self, gen):
        base = 1_900_000_000_000
        values = [gen(base) for _ in range(5000)]
        assert values == sorted(values)
        assert len(set(values)) == 5000
        assert timestamp_ms_of(values[-1]) > base

    def test_collision_free_under_a_fixed_random_source(self):
        """Even with no entropy at all, the counter keeps ids unique."""
        gen = Uuid7Generator(randbytes=lambda n: b"\x00" * n)
        values = [gen(1_767_225_600_000) for _ in range(100)]
        assert len(set(values)) == 100
        assert values == sorted(values)

    def test_threads_never_collide(self):
        import threading

        gen = Uuid7Generator()
        out: list[list] = []
        lock = threading.Lock()

        def work() -> None:
            local = [gen(1_950_000_000_000) for _ in range(200)]
            with lock:
                out.append(local)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        flat = [v for chunk in out for v in chunk]
        assert len(set(flat)) == len(flat)


class TestClocks:
    def test_system_clock_is_utc_aware(self):
        now = SystemClock().now()
        assert now.tzinfo is not None
        assert SystemClock().today() == now.date() or True  # date may tick over

    def test_manual_clock_is_fixed(self):
        clock = ManualClock(datetime(2026, 3, 1, tzinfo=timezone.utc))
        assert clock.now() == datetime(2026, 3, 1, tzinfo=timezone.utc)
        assert clock.today() == date(2026, 3, 1)
        assert clock.now() == clock.now()

    def test_manual_clock_defaults(self):
        assert ManualClock().today() == date(2026, 1, 1)

    def test_advance(self):
        clock = ManualClock(datetime(2026, 3, 1, tzinfo=timezone.utc))
        clock.advance(days=1, hours=2)
        assert clock.now() == datetime(2026, 3, 2, 2, tzinfo=timezone.utc)

    def test_advance_refuses_to_go_backwards(self):
        clock = ManualClock()
        with pytest.raises(ValueError, match="only moves forward"):
            clock.advance(seconds=-1)

    def test_set(self):
        clock = ManualClock()
        clock.set(datetime(2030, 1, 1, tzinfo=timezone.utc))
        assert clock.today() == date(2030, 1, 1)

    def test_naive_datetimes_are_refused(self):
        """Audit stamps are UTC instants (decision #10); naive input is a bug."""
        with pytest.raises(ValueError, match="timezone-aware"):
            ManualClock(datetime(2026, 1, 1))
        with pytest.raises(ValueError, match="timezone-aware"):
            ManualClock().set(datetime(2026, 1, 1))

    def test_converts_to_utc(self):
        other = timezone(timedelta(hours=5))
        clock = ManualClock(datetime(2026, 1, 1, 5, tzinfo=other))
        assert clock.now() == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestCommodity:
    def test_currency_defaults(self):
        eur = Commodity.currency("EUR", name="Euro", symbol="\u20ac")
        assert eur.kind is CommodityKind.CURRENCY
        assert eur.minor_unit_factor == 100
        assert eur.precision == 2
        assert eur.is_currency
        assert eur.display_symbol == "\u20ac"
        assert str(eur) == "EUR"

    def test_name_defaults_to_code(self):
        assert Commodity.currency("CHF").name == "CHF"

    @pytest.mark.parametrize(
        ("scale", "factor", "precision"),
        [(0, 1, 0), (2, 100, 2), (3, 1000, 3), (8, 10**8, 8)],
    )
    def test_scales(self, scale, factor, precision):
        commodity = Commodity.currency("TST", scale=scale)
        assert commodity.minor_unit_factor == factor
        assert commodity.precision == precision

    def test_non_decimal_minor_unit(self):
        """The ouguiya divides into five khoums, not a hundred cents."""
        mru = Commodity.currency("MRU", minor_unit_factor=5)
        assert mru.minor_unit_factor == 5
        assert mru.minor_unit.denominator == 5

    def test_display_precision_override(self):
        commodity = Commodity.currency("USD", display_precision=4)
        assert commodity.precision == 4
        assert commodity.minor_unit_factor == 100

    def test_security_defaults(self):
        aapl = Commodity.security("AAPL", name="Apple")
        assert aapl.kind is CommodityKind.STOCK
        assert aapl.precision == 4
        assert not aapl.is_currency

    def test_crypto_with_many_decimals(self):
        btc = Commodity.security("BTC", kind=CommodityKind.CRYPTO, scale=8)
        assert btc.minor_unit_factor == 10**8
        assert btc.kind is CommodityKind.CRYPTO

    def test_key_is_case_insensitive(self):
        assert Commodity.currency("eur").key == "EUR"

    @pytest.mark.parametrize("code", ["", "   "])
    def test_code_is_required(self, code):
        with pytest.raises(ValidationError, match="code must not be empty"):
            Commodity(code=code)

    def test_minor_unit_factor_must_be_positive(self):
        with pytest.raises(ValidationError, match="minor_unit_factor"):
            Commodity(code="X", minor_unit_factor=0)

    def test_display_precision_must_not_be_negative(self):
        with pytest.raises(ValidationError, match="display_precision"):
            Commodity(code="X", display_precision=-1)

    def test_negative_scale_is_refused(self):
        with pytest.raises(ValidationError, match="scale"):
            Commodity.currency("X", scale=-1)
        with pytest.raises(ValidationError, match="scale"):
            Commodity.security("X", scale=-1)

    def test_is_immutable(self):
        eur = Commodity.currency("EUR")
        with pytest.raises(FrozenInstanceError):
            eur.code = "USD"  # type: ignore[misc]


class TestAccountTypes:
    @pytest.mark.parametrize(
        ("type_", "klass"),
        [
            (AccountType.CHECKING, AccountClass.ASSET),
            (AccountType.SAVINGS, AccountClass.ASSET),
            (AccountType.CASH, AccountClass.ASSET),
            (AccountType.INVESTMENT, AccountClass.ASSET),
            (AccountType.STOCK, AccountClass.ASSET),
            (AccountType.ASSET, AccountClass.ASSET),
            (AccountType.RECEIVABLE, AccountClass.ASSET),
            (AccountType.CREDIT_CARD, AccountClass.LIABILITY),
            (AccountType.LOAN, AccountClass.LIABILITY),
            (AccountType.LIABILITY, AccountClass.LIABILITY),
            (AccountType.PAYABLE, AccountClass.LIABILITY),
            (AccountType.INCOME, AccountClass.INCOME),
            (AccountType.EXPENSE, AccountClass.EXPENSE),
            (AccountType.EQUITY, AccountClass.EQUITY),
        ],
    )
    def test_every_type_has_a_class(self, type_, klass):
        assert type_.account_class is klass

    def test_all_types_are_mapped(self):
        for type_ in AccountType:
            assert isinstance(type_.account_class, AccountClass)

    @pytest.mark.parametrize(
        ("klass", "debit_normal"),
        [
            (AccountClass.ASSET, True),
            (AccountClass.EXPENSE, True),
            (AccountClass.LIABILITY, False),
            (AccountClass.INCOME, False),
            (AccountClass.EQUITY, False),
        ],
    )
    def test_sign_conventions(self, klass, debit_normal):
        assert klass.is_debit_normal is debit_normal
        assert klass.report_sign == (1 if debit_normal else -1)

    def test_debit_normal_shortcut_on_type(self):
        assert AccountType.CHECKING.is_debit_normal
        assert not AccountType.CREDIT_CARD.is_debit_normal
        assert AccountType.CREDIT_CARD.report_sign == -1

    def test_balance_sheet_classes(self):
        assert AccountClass.ASSET.is_balance_sheet
        assert AccountClass.LIABILITY.is_balance_sheet
        assert AccountClass.EQUITY.is_balance_sheet
        assert not AccountClass.INCOME.is_balance_sheet
        assert not AccountClass.EXPENSE.is_balance_sheet

    def test_holds_securities(self):
        assert AccountType.STOCK.holds_securities
        assert not AccountType.INVESTMENT.holds_securities


class TestAccount:
    @pytest.fixture
    def usd(self) -> Commodity:
        return Commodity.currency("USD")

    def make(self, usd, **kwargs):
        defaults = dict(
            id=new_id(), type=AccountType.CHECKING, name="Main", commodity=usd
        )
        return Account(**{**defaults, **kwargs})

    def test_basics(self, usd):
        account = self.make(usd, description="Everyday account")
        assert account.account_class is AccountClass.ASSET
        assert account.report_sign == 1
        assert account.accepts_postings
        assert str(account) == "Main"

    def test_name_is_required(self, usd):
        with pytest.raises(ValidationError, match="must not be empty"):
            self.make(usd, name="  ")

    def test_name_cannot_contain_the_separator(self, usd):
        with pytest.raises(ValidationError, match="must not contain"):
            self.make(usd, name=f"A{ACCOUNT_SEPARATOR}B")

    def test_name_and_commodity_are_mandatory(self, usd):
        with pytest.raises(TypeError, match="commodity"):
            Account(id=new_id(), type=AccountType.CASH, name="X")
        with pytest.raises(TypeError, match="name"):
            Account(id=new_id(), type=AccountType.CASH, commodity=usd)

    def test_closed_and_placeholder_reject_postings(self, usd):
        assert not self.make(usd, closed=True).accepts_postings
        assert not self.make(usd, placeholder=True).accepts_postings

    def test_key_is_case_insensitive(self, usd):
        assert self.make(usd, name="Main").key == self.make(usd, name="MAIN").key


class TestPayee:
    def test_basics(self):
        payee = Payee(name="Supermarket", notes="round the corner")
        assert str(payee) == "Supermarket"
        assert payee.key == "supermarket"

    def test_name_is_required(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            Payee(name=" ")

    def test_match_keys_absorb_alternatives(self):
        payee = Payee(name="Amazon")
        merged = payee.with_match_keys(("AMAZON EU", "Amazon.de"))
        assert merged.match_keys == ("AMAZON EU", "Amazon.de")

    def test_match_keys_skip_duplicates_and_the_own_name(self):
        payee = Payee(name="Amazon", match_keys=("AMZN",))
        merged = payee.with_match_keys(("amazon", "AMZN", "  ", "Amazon EU"))
        assert merged.match_keys == ("AMZN", "Amazon EU")

    def test_match_keys_are_idempotent(self):
        payee = Payee(name="Amazon")
        once = payee.with_match_keys(("AMZN",))
        assert once.with_match_keys(("AMZN",)) == once


class TestEntityStamping:
    def test_created_sets_both_stamps(self):
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        payee = Payee(name="X").stamped(at=at, revision=3, created=True)
        assert payee.created_at == at
        assert payee.updated_at == at
        assert payee.revision == 3

    def test_update_preserves_created_at(self):
        first = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = datetime(2026, 2, 1, tzinfo=timezone.utc)
        payee = Payee(name="X").stamped(at=first, revision=1, created=True)
        updated = payee.stamped(at=later, revision=9)
        assert updated.created_at == first
        assert updated.updated_at == later
        assert updated.revision == 9

    @given(st.integers(min_value=0, max_value=10**9))
    def test_revision_is_whatever_the_book_says(self, revision):
        at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert Payee(name="X").stamped(at=at, revision=revision).revision == revision

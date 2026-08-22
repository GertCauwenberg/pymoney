"""Reading ``.kmy`` files: formats, rationals, sections, streaming."""

from __future__ import annotations

import gzip
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest

from pymoney.core import CommodityKind, ReconcileState
from pymoney.importers.kmymoney import KmyFile, KmyFormatError, detect_format
from pymoney.importers.kmymoney.parser import open_kmy, parse_rational

FIXTURE = Path(__file__).parent / "fixtures" / "household.kmy.xml"
F = Fraction


@pytest.fixture
def plain() -> Path:
    return FIXTURE


@pytest.fixture
def gzipped(tmp_path: Path) -> Path:
    """The same file as KMyMoney actually writes it: gzipped."""
    target = tmp_path / "household.kmy"
    target.write_bytes(gzip.compress(FIXTURE.read_bytes()))
    return target


@pytest.fixture
def source(plain: Path) -> KmyFile:
    return KmyFile.read(plain)


class TestFormatDetection:
    def test_plain_xml(self, plain):
        assert detect_format(plain) == "xml"

    def test_gzip(self, gzipped):
        assert detect_format(gzipped) == "gzip"

    def test_gpg_armoured(self, tmp_path):
        path = tmp_path / "secret.kmy"
        path.write_bytes(b"-----BEGIN PGP MESSAGE-----\nhQIMA...\n")
        assert detect_format(path) == "gpg"

    def test_gpg_binary(self, tmp_path):
        path = tmp_path / "secret.kmy"
        path.write_bytes(b"\x85\x02\x0c\x03")
        assert detect_format(path) == "gpg"

    def test_an_encrypted_file_says_what_to_do(self, tmp_path):
        """Not "unsupported format": the user needs the gpg command line."""
        path = tmp_path / "secret.kmy"
        path.write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
        with pytest.raises(KmyFormatError, match="gpg --decrypt"):
            with open_kmy(path):
                pass

    def test_the_passphrase_is_not_ours_to_handle(self, tmp_path):
        path = tmp_path / "secret.kmy"
        path.write_bytes(b"-----BEGIN PGP MESSAGE-----\n")
        with pytest.raises(KmyFormatError, match="passphrase"):
            with open_kmy(path):
                pass

    def test_empty(self, tmp_path):
        path = tmp_path / "empty.kmy"
        path.write_bytes(b"")
        with pytest.raises(KmyFormatError, match="is empty"):
            detect_format(path)

    def test_something_else_entirely(self, tmp_path):
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0JFIF")
        with pytest.raises(KmyFormatError, match="neither gzipped nor XML"):
            detect_format(path)

    def test_gzip_and_plain_parse_identically(self, plain, gzipped):
        first, second = KmyFile.read(plain), KmyFile.read(gzipped)
        assert first.accounts.keys() == second.accounts.keys()
        assert first.base_currency_id == second.base_currency_id
        assert [t.id for t in first.iter_transactions()] == [
            t.id for t in second.iter_transactions()
        ]

    def test_the_wrong_kind_of_xml(self, tmp_path):
        path = tmp_path / "other.xml"
        path.write_text('<?xml version="1.0"?><gnucash/>')
        with pytest.raises(KmyFormatError, match="KMYMONEY-FILE"):
            KmyFile.read(path)

    def test_xml_with_no_elements(self, tmp_path):
        path = tmp_path / "bare.xml"
        path.write_text('<?xml version="1.0"?><!-- nothing here -->')
        with pytest.raises(KmyFormatError, match="not valid XML"):
            KmyFile.read(path)

    def test_a_truncated_file_says_so(self, tmp_path):
        """Half a download should not surface an XML traceback."""
        path = tmp_path / "cut.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01"'
        )
        with pytest.raises(KmyFormatError, match="not valid XML"):
            KmyFile.read(path)

    def test_streaming_reports_a_truncation_too(self, tmp_path):
        """The two passes are independent, so each has to guard its own read."""
        path = tmp_path / "cut3.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><ACCOUNTS count="0"/></KMYMONEY-FILE>'
        )
        source = KmyFile.read(path)
        path.write_text('<?xml version="1.0"?><KMYMONEY-FILE><TRANSACTIONS ')
        with pytest.raises(KmyFormatError, match="not valid XML"):
            list(source.iter_transactions())

    def test_a_file_truncated_inside_the_transactions(self, tmp_path):
        path = tmp_path / "cut2.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><ACCOUNTS count="0"/>'
            '<TRANSACTIONS count="2">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="1/1" value="1/1"/></SPLITS></TRANSACTION>'
            "<TRANSACTION "
        )
        # The metadata pass hits the truncation first, so it fails there.
        with pytest.raises(KmyFormatError, match="not valid XML"):
            KmyFile.read(path)


class TestRationals:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("-8420/100", F(-8420, 100)),
            ("1000/300", F(10, 3)),
            ("5", F(5)),
            ("0/1", F(0)),
            ("1832433/10000", F(1832433, 10000)),
        ],
    )
    def test_parsed_exactly(self, raw, expected):
        """No decimals, no floats: 1000/300 is a third, not 3.33."""
        assert parse_rational(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "not a number", "1/0"])
    def test_unparseable_falls_back(self, raw):
        """One bad attribute must not abort a 100k-transaction import."""
        assert parse_rational(raw) is None
        assert parse_rational(raw, F(0)) == F(0)


class TestSections:
    def test_file_info(self, source):
        assert source.info.creation_date == date(2016, 3, 4)
        assert source.info.last_modified == date(2026, 2, 1)
        assert source.info.version == "1"
        assert source.info.fix_version == "5"

    def test_base_currency_comes_from_the_file_settings(self, source):
        assert source.base_currency_id == "EUR"

    def test_currencies(self, source):
        assert set(source.currencies) == {"EUR", "USD", "JPY"}
        eur = source.currencies["EUR"]
        assert eur.name == "Euro"
        assert eur.symbol == "\u20ac"
        assert eur.smallest_account_fraction == 100
        assert eur.kind is CommodityKind.CURRENCY
        assert source.currencies["JPY"].smallest_account_fraction == 1

    def test_securities(self, source):
        aapl = source.securities["E000001"]
        assert aapl.symbol == "AAPL"
        assert aapl.name == "Apple Inc."
        assert aapl.smallest_account_fraction == 10000
        assert aapl.price_precision == 4
        assert aapl.trading_currency == "USD"
        assert aapl.isin == "US0378331005"
        assert aapl.kind is CommodityKind.STOCK
        assert not aapl.is_currency

    def test_accounts(self, source):
        account = source.accounts["A000001"]
        assert account.name == "Current Account"
        assert account.parent_id == "AStd::Asset"
        assert account.type == 1
        assert account.currency_id == "EUR"
        assert account.number == "12345678"
        assert account.institution_id == "I000001"
        assert account.opened == date(2016, 3, 4)
        assert account.iban == "DE02120300000000202051"
        assert not account.closed

    def test_closed_accounts_are_recognised(self, source):
        assert source.accounts["A000002"].closed

    def test_standard_accounts_are_recognised(self, source):
        assert not source.accounts["A000001"].is_standard

    def test_key_values_are_kept_whole(self, source):
        """So the importer can report the settings it does not understand."""
        pairs = source.accounts["A000001"].key_values
        assert pairs["lastNumberUsed"] == "104"
        assert pairs["PreferredAccount"] == "Yes"

    def test_payees(self, source):
        payee = source.payees["P000001"]
        assert payee.name == "Supermarket"
        assert "Market Row" in payee.address
        assert source.payees["P000002"].email == "payroll@acme.invalid"

    def test_institutions(self, source):
        assert source.institutions["I000001"].name == "Example Bank"

    def test_tags(self, source):
        assert source.tags == {"G000001": "holiday", "G000002": "work"}

    def test_prices(self, source):
        assert len(source.prices) == 3
        usd_eur = [p for p in source.prices if p.from_id == "USD"]
        assert {p.date for p in usd_eur} == {date(2026, 1, 31), date(2026, 1, 12)}
        assert usd_eur[0].rate == F(9174, 10000)
        assert usd_eur[0].source == "KMyMoney"

    def test_declared_counts(self, source):
        assert source.declared_counts["transactions"] == 15
        assert source.declared_counts["schedules"] == 2
        assert source.declared_counts["budgets"] == 1

    def test_commodity_lookup_by_id_and_by_symbol(self, source):
        assert source.commodity("EUR").id == "EUR"
        assert source.commodity("E000001").symbol == "AAPL"
        # Some files reference a security by its trading symbol instead.
        assert source.commodity("AAPL").id == "E000001"
        assert source.commodity("nonsense") is None
        assert source.commodity("") is None

    def test_commodities_are_one_namespace(self, source):
        assert set(source.commodities) == {"EUR", "USD", "JPY", "E000001"}


class TestTransactionStreaming:
    def test_all_of_them(self, source):
        transactions = list(source.iter_transactions())
        assert len(transactions) == 15
        assert transactions[0].id == "T000000000000000001"

    def test_scheduled_transactions_are_not_real_ones(self, source):
        """A SCHEDULED_TX contains a TRANSACTION: the template for a future
        payment that has not happened. Importing those would invent money."""
        ids = {t.id for t in source.iter_transactions()}
        assert all(i.startswith("T") for i in ids)
        assert "" not in ids
        assert len(ids) == 15
        assert source.declared_counts["schedules"] == 2

    def test_splits_keep_their_exact_rationals(self, source):
        taxi = next(
            t for t in source.iter_transactions() if t.memo == "Taxi split three ways"
        )
        thirds = [s for s in taxi.splits if s.memo == "one third"]
        assert len(thirds) == 3
        assert all(s.shares == F(10, 3) for s in thirds)
        assert taxi.value_total == 0

    def test_split_attributes(self, source):
        shop = next(t for t in source.iter_transactions() if t.memo == "Weekly shop")
        card = shop.splits[0]
        assert card.account_id == "A000001"
        assert card.payee_id == "P000001"
        assert card.shares == F(-8420, 100)
        assert card.value == F(-8420, 100)
        assert card.memo == "card"
        assert card.number == "101"
        assert card.bank_id == "ID-0001"
        assert card.reconcile_state is ReconcileState.RECONCILED
        assert card.reconcile_date == date(2026, 1, 31)

    def test_reconcile_flags(self, source):
        salary = next(t for t in source.iter_transactions() if t.memo == "January salary")
        assert salary.splits[0].reconcile_state is ReconcileState.CLEARED
        assert salary.splits[1].reconcile_state is ReconcileState.NOT_RECONCILED

    def test_multi_currency_split(self, source):
        fx = next(t for t in source.iter_transactions() if t.memo == "Buy dollars")
        usd_leg = fx.splits[1]
        assert usd_leg.shares == F(10000, 100)
        assert usd_leg.value == F(9174, 100)
        assert usd_leg.price == F(9174, 10000)
        assert fx.currency_id == "EUR"

    def test_investment_action(self, source):
        buy = next(t for t in source.iter_transactions() if t.memo == "Buy 12 AAPL")
        assert buy.splits[0].action == "Buy"
        assert buy.splits[0].shares == F(12)

    def test_streaming_is_repeatable(self, source):
        """The file is read again each time; no state is consumed."""
        first = [t.id for t in source.iter_transactions()]
        second = [t.id for t in source.iter_transactions()]
        assert first == second

    def test_a_transaction_with_no_splits(self, tmp_path):
        path = tmp_path / "odd.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"/>'
            "</TRANSACTIONS></KMYMONEY-FILE>"
        )
        transactions = list(KmyFile.read(path).iter_transactions())
        assert transactions[0].splits == ()

    def test_splits_without_ids_get_positional_ones(self, tmp_path):
        path = tmp_path / "noids.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><TRANSACTIONS count="1">'
            '<TRANSACTION id="T1" postdate="2026-01-01" commodity="EUR"><SPLITS>'
            '<SPLIT account="A1" shares="1/1" value="1/1"/>'
            '<SPLIT account="A2" shares="-1/1" value="-1/1"/>'
            "</SPLITS></TRANSACTION></TRANSACTIONS></KMYMONEY-FILE>"
        )
        splits = next(iter(KmyFile.read(path).iter_transactions())).splits
        assert [s.id for s in splits] == ["S0000", "S0001"]


class TestMissingSections:
    def test_a_nearly_empty_file(self, tmp_path):
        path = tmp_path / "new.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<FILEINFO><VERSION id="1"/></FILEINFO>'
            '<ACCOUNTS count="0"/><TRANSACTIONS count="0"/>'
            "</KMYMONEY-FILE>"
        )
        source = KmyFile.read(path)
        assert source.accounts == {}
        assert list(source.iter_transactions()) == []
        assert source.base_currency_id == ""
        assert source.info.version == "1"

    def test_malformed_dates_and_numbers_are_tolerated(self, tmp_path):
        """A hand-edited file should degrade, not explode."""
        path = tmp_path / "messy.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE>'
            '<CURRENCIES count="1"><CURRENCY id="EUR" saf="not-a-number"/></CURRENCIES>'
            '<ACCOUNTS count="1"><ACCOUNT id="A1" name="X" type="oops" '
            'opened="the fourth of March"/></ACCOUNTS>'
            "</KMYMONEY-FILE>"
        )
        source = KmyFile.read(path)
        assert source.currencies["EUR"].smallest_account_fraction == 100
        assert source.accounts["A1"].type == 0
        assert source.accounts["A1"].opened is None

    def test_prices_with_nonsense_are_dropped(self, tmp_path):
        path = tmp_path / "prices.xml"
        path.write_text(
            '<?xml version="1.0"?><KMYMONEY-FILE><PRICES count="1">'
            '<PRICEPAIR from="USD" to="EUR">'
            '<PRICE date="2026-01-01" price="9/10" source="ok"/>'
            '<PRICE date="not-a-date" price="9/10"/>'
            '<PRICE date="2026-01-02" price="0/1"/>'
            '<PRICE date="2026-01-03" price="-9/10"/>'
            "</PRICEPAIR></PRICES></KMYMONEY-FILE>"
        )
        prices = KmyFile.read(path).prices
        assert len(prices) == 1
        assert prices[0].source == "ok"

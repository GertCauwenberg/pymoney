"""The bank-statement (CSV) importer.

The KMyMoney importer is the one-time migration; this is the every-week one.
Its two load-bearing promises are tested here: re-importing an overlapping
download never doubles a transaction (the ``imported_id``), and the far side of
a transfer that is already in the book is not posted a second time.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from pymoney.core.posting import LegSpec
from pymoney.importers.statement import (
    AmountKind,
    ColumnMapping,
    ProposalStatus,
    RowDecision,
    StatementFormatError,
    entries_from_rows,
    import_statement,
    propose,
    read_csv,
    suggest_for,
    suggest_mapping,
)
from pymoney.importers.statement.entry import IMPORT_PREFIX, imported_id_for
from pymoney.importers.statement.mapping import parse_amount, parse_date
from pymoney.importers.statement.matching import clean_payee_name
from tests.conftest import Chart

F = Fraction


def _entries(chart: Chart, csv: bytes, *, account=None, **read_kwargs):
    read = read_csv(csv, **read_kwargs)
    mapping = suggest_mapping(read)
    return (
        entries_from_rows(
            read.rows,
            mapping,
            account_id=(account or chart.checking).id,
            columns=read.columns,
        ),
        mapping,
        read,
    )


# ==========================================================================
# reading a value out of a cell
# ==========================================================================
class TestParsing:
    @pytest.mark.parametrize(
        ("text", "decimal", "expected"),
        [
            ("1234.56", ".", F(123456, 100)),
            ("1,234.56", ".", F(123456, 100)),
            ("1.234,56", ",", F(123456, 100)),
            ("1 234,56", ",", F(123456, 100)),
            ("-42.11", ".", F(-4211, 100)),
            ("(42.11)", ".", F(-4211, 100)),
            ("42.11-", ".", F(-4211, 100)),
            ("  $1,000.00 ", ".", F(1000)),
            ("EUR 12,50", ",", F(25, 2)),
            ("100.00 CR", ".", F(100)),
            ("100.00 DR", ".", F(-100)),
            ("", ".", F(0)),
            ("   ", ",", F(0)),
        ],
    )
    def test_amounts(self, text, decimal, expected):
        assert parse_amount(text, decimal) == expected

    @pytest.mark.parametrize("text", ["twelve", "1.2.3", "--5", "1/0", "."])
    def test_an_unparseable_amount_names_the_row(self, text):
        with pytest.raises(StatementFormatError, match="row 7"):
            parse_amount(text, ".", line=7)

    def test_a_european_amount_read_as_anglo_is_caught(self):
        with pytest.raises(StatementFormatError, match="separator"):
            parse_amount("1.234,56", ".")

    @pytest.mark.parametrize(
        ("text", "fmt", "expected"),
        [
            ("2026-02-03", "", date(2026, 2, 3)),
            ("03/02/2026", "", date(2026, 2, 3)),
            ("02/03/2026", "%m/%d/%Y", date(2026, 2, 3)),
            ("3 Feb 2026", "", date(2026, 2, 3)),
            ("20260203", "", date(2026, 2, 3)),
        ],
    )
    def test_dates(self, text, fmt, expected):
        assert parse_date(text, fmt) == expected

    def test_an_unparseable_date_names_the_row(self):
        with pytest.raises(StatementFormatError, match="row 2"):
            parse_date("not a date", "", line=2)

    def test_a_mapping_needs_a_date_and_an_amount(self):
        with pytest.raises(StatementFormatError, match="date column"):
            ColumnMapping(date="", amount="Amount")
        with pytest.raises(StatementFormatError, match="amount column"):
            ColumnMapping(date="Date", amount="")
        with pytest.raises(StatementFormatError, match=r"debit.*credit"):
            ColumnMapping(date="Date", amount_kind=AmountKind.DEBIT_CREDIT)

    def test_a_mapping_that_names_a_missing_column_says_which(self, chart: Chart):
        csv = b"Date,Amount\n2026-02-03,-5.00\n"
        mapping = ColumnMapping(date="Date", amount="Amount", description=("Memo",))
        with pytest.raises(StatementFormatError, match="'Memo'"):
            entries_from_rows(
                read_csv(csv).rows,
                mapping,
                account_id=chart.checking.id,
                columns=("Date", "Amount"),
            )

    def test_clean_payee_name_strips_the_noise(self):
        assert clean_payee_name("SEPA OVERSCHRIJVING ALBERT HEIJN 1234") == "Albert Heijn"
        assert clean_payee_name("CARD 12.34 AMZN MKTP DE 2026-02-01") == "Amzn Mktp"
        assert clean_payee_name("acme payroll") == "acme payroll"
        assert clean_payee_name("POS 3344") == "Pos 3344"  # nothing left: keep it all
        assert clean_payee_name("") == ""

    def test_clean_payee_name_keeps_the_middle(self):
        cleaned = clean_payee_name("POS TESCO STORES 2243 LONDON GB")
        assert cleaned.startswith("Tesco Stores") and "London" in cleaned
        assert not cleaned.endswith("GB")


# ==========================================================================
# sniffing the file
# ==========================================================================
class TestReadAndSuggest:
    def test_a_comma_file_with_a_header(self):
        read = read_csv(
            b"Date,Description,Amount\n2026-02-03,SHOP,-5.00\n2026-02-04,PAY,9.00\n"
        )
        assert read.columns == ("Date", "Description", "Amount")
        assert read.row_count == 2
        assert read.has_header and read.delimiter == ","
        assert read.rows[0]["Description"] == "SHOP"

    def test_semicolons_commas_decimals_and_no_header(self):
        read = read_csv(b"03-02-2026;SHOP ONE;-12,50\n04-02-2026;SHOP TWO;-7,00\n")
        assert read.delimiter == ";" and not read.has_header
        assert read.columns == ("1", "2", "3")
        mapping = suggest_mapping(read)
        assert mapping.decimal == "," and mapping.date == "1" and mapping.amount == "3"

    def test_a_preamble_block_is_skipped(self):
        csv = (
            b"Account summary for 12345678\n"
            b"Generated 2026-03-01\n"
            b"\n"
            b"Date,Description,Amount\n"
            b"2026-02-03,SHOP,-5.00\n"
            b"2026-02-28,SHOP,-6.00\n"
        )
        read = read_csv(csv)
        assert read.skip_rows == 3
        assert read.columns == ("Date", "Description", "Amount")
        assert read.row_count == 2

    def test_latin1_is_decoded_not_mangled(self):
        csv = "Date,Description,Amount\n2026-02-03,CAFÉ MÜNCHEN,-4.20\n".encode("cp1252")
        read = read_csv(csv)
        assert read.rows[0]["Description"] == "CAFÉ MÜNCHEN"

    def test_debit_and_credit_columns_are_recognised(self):
        read = read_csv(
            b"Date,Details,Money Out,Money In\n"
            b"2026-02-03,SHOP,5.00,\n"
            b"2026-02-04,SALARY,,2500.00\n"
        )
        mapping = suggest_mapping(read)
        assert mapping.amount_kind is AmountKind.DEBIT_CREDIT
        assert mapping.debit == "Money Out" and mapping.credit == "Money In"

    def test_the_transaction_amount_is_preferred_over_the_balance(self):
        read = read_csv(
            b"Date,Amount,Balance\n2026-02-03,-5.00,995.00\n2026-02-04,9.00,1004.00\n"
        )
        assert suggest_mapping(read).amount == "Amount"

    def test_the_round_trip_through_a_dict_mapping(self):
        read = read_csv(b"Date,Info,Amount\n2026-02-03,SHOP,-5.00\n")
        before = suggest_mapping(read)
        after = ColumnMapping(
            **{k: getattr(before, k) for k in before.__dataclass_fields__}
        )
        assert after == before

    def test_an_empty_file_is_refused(self):
        with pytest.raises(StatementFormatError, match="no data"):
            read_csv(b"   \n \n")


# ==========================================================================
# canonical entries and the dedup key
# ==========================================================================
class TestEntries:
    def test_signs_debit_credit_and_invert(self, chart: Chart):
        csv = b"Date,Details,Out,In\n2026-02-03,SHOP,12.34,\n2026-02-04,SALARY,,2500.00\n"
        read = read_csv(csv)
        mapping = suggest_mapping(read)
        entries = entries_from_rows(
            read.rows, mapping, account_id=chart.checking.id, columns=read.columns
        )
        assert [e.amount for e in entries] == [F(-1234, 100), F(2500)]

        inverted = ColumnMapping(
            **{
                **{k: getattr(mapping, k) for k in mapping.__dataclass_fields__},
                "invert": True,
            }
        )
        flipped = entries_from_rows(
            read.rows, inverted, account_id=chart.checking.id, columns=read.columns
        )
        assert [e.amount for e in flipped] == [F(1234, 100), F(-2500)]

    def test_reimporting_the_same_file_yields_the_same_ids(self, chart: Chart):
        csv = b"Date,Details,Amount\n2026-02-03,SHOP,-5.00\n2026-02-04,PAY,9.00\n"
        first, _, _ = _entries(chart, csv)
        again, _, _ = _entries(chart, csv)
        assert [e.imported_id for e in first] == [e.imported_id for e in again]
        assert all(e.imported_id.startswith(IMPORT_PREFIX) for e in first)

    def test_identical_rows_get_distinct_ids_by_order(self, chart: Chart):
        csv = b"Date,Details,Amount\n2026-02-03,COFFEE,-3.50\n2026-02-03,COFFEE,-3.50\n"
        entries, _, _ = _entries(chart, csv)
        assert entries[0].imported_id != entries[1].imported_id

    def test_a_bank_reference_pins_the_id_regardless_of_order(self, chart: Chart):
        a = imported_id_for(
            chart.checking.id,
            on=date(2026, 2, 3),
            amount=F(-5),
            reference="TXN-99",
            description="SHOP",
            occurrence=0,
        )
        b = imported_id_for(
            chart.checking.id,
            on=date(2026, 2, 3),
            amount=F(-5),
            reference="TXN-99",
            description="different text",
            occurrence=3,
        )
        assert a == b

    def test_the_id_is_scoped_to_the_account(self, chart: Chart):
        common = dict(
            on=date(2026, 2, 3),
            amount=F(-5),
            reference="",
            description="SHOP",
            occurrence=0,
        )
        assert imported_id_for(chart.checking.id, **common) != imported_id_for(
            chart.savings.id, **common
        )


# ==========================================================================
# proposing what to do with each row
# ==========================================================================
class TestProposals:
    def test_heuristics_in_priority_order(self, chart: Chart):
        book = chart.book
        book.add_payee(
            "Tesco",
            match_keys=("TESCO STORES",),
            default_account_id=chart.groceries.id,
        )
        ah = book.add_payee("Albert Heijn", match_keys=("ALBERT HEIJN",))
        book.post(
            date(2026, 1, 9),
            [LegSpec.of(chart.checking.id, "-20.00"), LegSpec.of(chart.fuel.id)],
            payee_id=ah.id,
        )
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,TESCO STORES 7 LONDON,-42.11\n"  # payee default
            b"2026-02-04,ALBERT HEIJN 118 UTRECHT,-31.20\n"  # last used
            b"2026-02-05,A SHOP NOBODY KNOWS,-9.99\n"  # new payee
        )
        entries, _, _ = _entries(chart, csv)
        proposals = propose(book, chart.checking, entries)

        grocery, fuel, unknown = proposals
        assert grocery.suggestion.reason == "payee default"
        assert grocery.suggestion.category_id == chart.groceries.id
        assert fuel.suggestion.reason == "last used"
        assert fuel.suggestion.category_id == chart.fuel.id
        assert unknown.suggestion.is_new_payee
        assert unknown.suggestion.payee_name == "A Shop Nobody Knows"
        assert all(p.status is ProposalStatus.NEW for p in proposals)

    def test_an_already_imported_row_is_a_duplicate(self, chart: Chart):
        csv = b"Date,Details,Amount\n2026-02-03,SHOP,-5.00\n2026-02-04,PAY,9.00\n"
        entries, _, _ = _entries(chart, csv)
        import_statement(
            chart.book,
            chart.checking,
            entries,
            {},
            uncategorised_account_id=chart.fuel.id,
        )
        again = propose(chart.book, chart.checking, entries)
        assert [p.status for p in again] == [ProposalStatus.DUPLICATE] * 2
        assert all(p.default_skip for p in again)

    def test_a_transfer_already_on_the_other_account_is_flagged(self, chart: Chart):
        # Savings shows the transfer; Checking does not, yet.
        chart.book.post(
            date(2026, 2, 10),
            [LegSpec.of(chart.savings.id, "300.00"), LegSpec.of(chart.opening.id)],
            memo="from current account",
        )
        csv = b"Date,Details,Amount\n2026-02-11,MOVE TO SAVINGS,-300.00\n"
        entries, _, _ = _entries(chart, csv)
        (proposal,) = propose(chart.book, chart.checking, entries)
        assert proposal.status is ProposalStatus.TRANSFER
        assert proposal.existing_account_id == chart.savings.id
        assert proposal.suggestion.category_id == chart.savings.id

    def test_a_hand_entered_transaction_is_seen_as_a_duplicate(self, chart: Chart):
        chart.book.post(
            date(2026, 2, 3),
            [LegSpec.of(chart.checking.id, "-42.11"), LegSpec.of(chart.groceries.id)],
            memo="Tesco, typed in before the download arrived",
        )
        csv = b"Date,Details,Amount\n2026-02-03,TESCO STORES,-42.11\n"
        entries, _, _ = _entries(chart, csv)
        (proposal,) = propose(chart.book, chart.checking, entries)
        assert proposal.status is ProposalStatus.DUPLICATE

    def test_each_existing_transaction_is_matched_at_most_once(self, chart: Chart):
        for _ in range(2):
            chart.book.post(
                date(2026, 2, 3),
                [LegSpec.of(chart.checking.id, "-3.50"), LegSpec.of(chart.fuel.id)],
            )
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,COFFEE,-3.50\n"
            b"2026-02-03,COFFEE,-3.50\n"
            b"2026-02-03,COFFEE,-3.50\n"
        )
        entries, _, _ = _entries(chart, csv)
        statuses = [p.status for p in propose(chart.book, chart.checking, entries)]
        assert statuses == [
            ProposalStatus.DUPLICATE,
            ProposalStatus.DUPLICATE,
            ProposalStatus.NEW,
        ]


# ==========================================================================
# posting the reviewed statement
# ==========================================================================
class TestImport:
    def test_it_posts_the_rows_with_payee_and_category(self, chart: Chart):
        tesco = chart.book.add_payee("Tesco", match_keys=("TESCO STORES",))
        csv = (
            b"Date,Details,Amount,Ref\n"
            b"2026-02-03,TESCO STORES 7,-42.11,TXN-1\n"
            b"2026-02-04,AN UNKNOWN SHOP,-9.99,TXN-2\n"
        )
        entries, _, _ = _entries(chart, csv)
        decisions = {
            entries[0].imported_id: RowDecision(
                row_key=entries[0].imported_id,
                payee_id=tesco.id,
                category_id=chart.groceries.id,
            ),
            entries[1].imported_id: RowDecision(
                row_key=entries[1].imported_id,
                new_payee="Corner Shop",
                category_id=chart.fuel.id,
            ),
        }
        before = chart.book.revision
        report = import_statement(chart.book, chart.checking, entries, decisions)

        assert report.posted == 2 and report.payees_created == 1
        assert report.revision_before == before and report.revision_after > before
        assert chart.book.balance(chart.checking.id) == chart.book.balance(
            chart.checking.id
        )
        register = chart.book.register(chart.checking.id)
        assert [r.quantity.minor for r in register] == [-4211, -999]
        txn = chart.book.transaction(register[0].transaction.id)
        assert txn.payee_id == tesco.id
        assert txn.imported_id == entries[0].imported_id
        assert txn.number == "TXN-1"
        assert chart.book.find_payee("Corner Shop") is not None

    def test_one_bad_row_is_reported_and_the_rest_go_in(self, chart: Chart):
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,GOOD,-5.00\n"
            b"2026-02-04,BAD,-7.00\n"
            b"2026-02-05,ALSO GOOD,-9.00\n"
        )
        entries, _, _ = _entries(chart, csv)
        decisions = {
            entries[1].imported_id: RowDecision(
                row_key=entries[1].imported_id,
                category_id=chart.assets.id,  # a placeholder: refuses a posting
            )
        }
        report = import_statement(
            chart.book,
            chart.checking,
            entries,
            decisions,
            uncategorised_account_id=chart.fuel.id,
        )
        assert report.posted == 2
        assert len(report.errors) == 1 and "BAD" in report.errors[0].message
        assert not report.ok
        assert chart.book.balance(chart.checking.id).minor == -1400

    def test_an_uncategorised_row_lands_in_the_fallback_account(self, chart: Chart):
        csv = b"Date,Details,Amount\n2026-02-03,MYSTERY,-15.00\n"
        entries, _, _ = _entries(chart, csv)
        report = import_statement(
            chart.book,
            chart.checking,
            entries,
            {},
            uncategorised_account_id=chart.fuel.id,
        )
        assert report.posted == 1 and report.uncategorised == 1
        assert chart.book.balance(chart.fuel.id).minor == 1500

    def test_a_missing_fallback_fails_just_that_row(self, chart: Chart):
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,HAS A HOME,-5.00\n"
            b"2026-02-04,HOMELESS,-6.00\n"
        )
        entries, _, _ = _entries(chart, csv)
        report = import_statement(
            chart.book,
            chart.checking,
            entries,
            {
                entries[0].imported_id: RowDecision(
                    row_key=entries[0].imported_id, category_id=chart.fuel.id
                )
            },
        )
        assert report.posted == 1 and len(report.errors) == 1
        assert "uncategorised" in report.errors[0].message

    def test_the_whole_import_is_one_unit_of_work(self, chart: Chart, monkeypatch):
        csv = b"Date,Details,Amount\n2026-02-03,ONE,-5.00\n2026-02-04,TWO,-6.00\n"
        entries, _, _ = _entries(chart, csv)
        before = chart.book.revision
        real_post = type(chart.book).post

        def explode(self, *a, **k):
            real_post(self, *a, **k)
            raise RuntimeError("the machine caught fire")

        monkeypatch.setattr(type(chart.book), "post", explode)
        with pytest.raises(RuntimeError):
            import_statement(
                chart.book,
                chart.checking,
                entries,
                {},
                uncategorised_account_id=chart.fuel.id,
            )
        assert chart.book.revision == before
        assert chart.book.register(chart.checking.id) == ()

    def test_defaults_skip_duplicates_and_transfers_and_post_the_rest(self, chart: Chart):
        chart.book.post(
            date(2026, 2, 3),
            [LegSpec.of(chart.savings.id, "100.00"), LegSpec.of(chart.opening.id)],
        )
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,MOVE TO SAVINGS,-100.00\n"  # transfer -> skipped
            b"2026-02-04,A NEW EXPENSE,-8.00\n"  # new -> posted
        )
        entries, _, _ = _entries(chart, csv)
        report = import_statement(
            chart.book,
            chart.checking,
            entries,
            {},
            uncategorised_account_id=chart.fuel.id,
        )
        assert report.posted == 1 and report.skipped_transfers == 1
        assert chart.book.balance(chart.checking.id).minor == -800

    def test_reimporting_a_statement_changes_nothing(self, chart: Chart):
        csv = b"Date,Details,Amount\n2026-02-03,SHOP,-5.00\n2026-02-10,PAY,9.00\n"
        entries, _, _ = _entries(chart, csv)
        first = import_statement(
            chart.book,
            chart.checking,
            entries,
            {},
            uncategorised_account_id=chart.fuel.id,
        )
        revision = chart.book.revision
        again_entries, _, _ = _entries(chart, csv)
        second = import_statement(
            chart.book,
            chart.checking,
            again_entries,
            {},
            uncategorised_account_id=chart.fuel.id,
        )
        assert first.posted == 2 and second.posted == 0
        assert second.skipped_duplicates == 2
        assert chart.book.revision == revision

    def test_suggest_for_is_the_one_row_form_of_the_matcher(self, chart: Chart):
        chart.book.add_payee(
            "Tesco", match_keys=("TESCO STORES",), default_account_id=chart.groceries.id
        )
        suggestion = suggest_for(chart.book, chart.checking, "TESCO STORES 12 LEEDS")
        assert suggestion.category_id == chart.groceries.id

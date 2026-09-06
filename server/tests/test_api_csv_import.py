"""CSV statement import over HTTP: inspect, preview, commit, remembered mapping.

The importer's own behaviour is exercised in ``test_statement_importer``; this
fixes the wire contract -- the three stateless calls, the multipart shape, the
auth, and the per-account mapping that survives to the next download.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymoney.api.testing import test_app as build_app
from tests.conftest_api import Client, Ledger

SIMPLE = (
    b"Date,Details,Amount\n"
    b"2026-02-03,TESCO STORES 12 LEEDS GB,-42.11\n"
    b"2026-02-04,ACME CORP SALARY,2500.00\n"
    b"2026-02-05,A CAFE NOBODY KNOWS,-3.80\n"
)
EUROPEAN = (
    b"Datum;Omschrijving;Bedrag\n"
    b"05-02-2026;ALBERT HEIJN 1234;-18,40\n"
    b"06-02-2026;SALARIS;2.500,00\n"
)


class Importer:
    """The three endpoints, for one account, as one object."""

    def __init__(self, client: Client, book_id: str, account_id: str) -> None:
        self.client = client
        self.book_id = book_id
        self.account_id = account_id

    def inspect(self, csv: bytes):
        return self.client.http.post(
            f"/books/{self.book_id}/imports/csv/inspect",
            files={"file": ("statement.csv", csv, "text/csv")},
            headers=self.client.headers,
        )

    def preview(self, csv: bytes, mapping: dict):
        return self.client.http.post(
            f"/books/{self.book_id}/accounts/{self.account_id}/imports/csv/preview",
            files={"file": ("statement.csv", csv, "text/csv")},
            data={"mapping": json.dumps(mapping)},
            headers=self.client.headers,
        )

    def commit(self, csv: bytes, mapping: dict, decisions=None, fallback=None):
        data = {"mapping": json.dumps(mapping)}
        if decisions is not None:
            data["decisions"] = json.dumps(decisions)
        if fallback is not None:
            data["uncategorised_account_id"] = fallback
        return self.client.http.post(
            f"/books/{self.book_id}/accounts/{self.account_id}/imports/csv/commit",
            files={"file": ("statement.csv", csv, "text/csv")},
            data=data,
            headers=self.client.headers,
        )

    def get_mapping(self):
        return self.client.get(
            f"/books/{self.book_id}/accounts/{self.account_id}/import-mapping"
        )

    def put_mapping(self, mapping: dict):
        return self.client.put(
            f"/books/{self.book_id}/accounts/{self.account_id}/import-mapping",
            json=mapping,
        )

    def suggested(self, csv: bytes) -> dict:
        return self.inspect(csv).json()["suggested_mapping"]


@pytest.fixture
def imp(ledger: Ledger) -> Importer:
    return Importer(ledger.client, ledger.book_id, ledger.checking)


@pytest.fixture
def small_limit(tmp_path: Path) -> Importer:
    """An importer on a server that accepts at most a one-megabyte upload."""
    with build_app(tmp_path, max_upload_mb=1) as client:
        client.register("tight@example.invalid")
        book_id = client.make_book()
        account = client.account("Cash", "CASH")
        yield Importer(client, book_id, account["id"])


class TestInspect:
    def test_it_sniffs_the_shape_and_suggests_a_mapping(self, imp: Importer):
        body = imp.inspect(SIMPLE).json()
        assert body["columns"] == ["Date", "Details", "Amount"]
        assert body["row_count"] == 3 and body["has_header"] and body["delimiter"] == ","
        assert body["sample_rows"][0]["Details"] == "TESCO STORES 12 LEEDS GB"
        mapping = body["suggested_mapping"]
        assert mapping["date"] == "Date" and mapping["amount"] == "Amount"
        assert mapping["description"] == ["Details"] and mapping["decimal"] == "."

    def test_a_european_file_is_read_correctly(self, imp: Importer):
        mapping = imp.suggested(EUROPEAN)
        assert mapping["delimiter"] == ";" and mapping["decimal"] == ","
        proposals = imp.preview(EUROPEAN, mapping).json()["proposals"]
        assert [p["amount"]["minor"] for p in proposals] == [-1840, 250000]

    def test_an_unreadable_upload_is_a_422(self, imp: Importer):
        assert imp.inspect(b"\n\n \n").status_code == 422

    def test_the_upload_size_is_bounded(self, small_limit: Importer):
        over_a_megabyte = b"Date,Details,Amount\n" + b"2026-02-03,X,-1.00\n" * 60_000
        assert len(over_a_megabyte) > 1024 * 1024
        assert small_limit.inspect(over_a_megabyte).status_code == 413


class TestPreview:
    def test_rows_are_classified_and_a_payee_is_guessed(self, ledger: Ledger):
        imp = Importer(ledger.client, ledger.book_id, ledger.checking)
        ledger.client.post(
            f"/books/{ledger.book_id}/payees",
            json={
                "name": "Tesco",
                "match_keys": ["TESCO STORES"],
                "default_account_id": ledger.groceries,
            },
        )
        response = imp.preview(SIMPLE, imp.suggested(SIMPLE))
        assert response.status_code == 200
        by_date = {p["date"]: p for p in response.json()["proposals"]}
        tesco = by_date["2026-02-03"]
        assert tesco["status"] == "new" and tesco["default_action"] == "post"
        assert tesco["suggested_category_id"] == ledger.groceries
        assert tesco["suggestion_reason"] == "payee default"
        unknown = by_date["2026-02-05"]
        assert unknown["suggested_payee_id"] is None
        assert unknown["suggested_payee_name"] and unknown["suggestion_reason"] == (
            "new payee"
        )

    def test_an_already_imported_statement_is_all_duplicates(self, imp: Importer):
        mapping = imp.suggested(SIMPLE)
        imp.commit(SIMPLE, mapping, fallback=None, decisions=_categorise(imp, SIMPLE))
        proposals = imp.preview(SIMPLE, mapping).json()["proposals"]
        assert {p["status"] for p in proposals} == {"duplicate"}
        assert all(p["default_action"] == "skip" for p in proposals)
        assert all(p["existing_transaction_id"] for p in proposals)

    def test_a_transfer_already_on_the_other_account_is_flagged(self, ledger: Ledger):
        imp = Importer(ledger.client, ledger.book_id, ledger.checking)
        ledger.client.post_transaction(
            "2026-02-10",
            [
                {"account_id": ledger.savings, "value": "300.00"},
                {"account_id": ledger.opening},
            ],
            memo="from current account",
        )
        csv = b"Date,Details,Amount\n2026-02-11,MOVE TO SAVINGS,-300.00\n"
        (proposal,) = imp.preview(csv, imp.suggested(csv)).json()["proposals"]
        assert proposal["status"] == "transfer"
        assert proposal["existing_account_id"] == ledger.savings
        assert proposal["suggested_category_id"] == ledger.savings

    def test_a_placeholder_account_is_refused(self, ledger: Ledger):
        accounts = ledger.client.get(f"/books/{ledger.book_id}/accounts").json()
        placeholder = next(a for a in accounts if a["placeholder"])
        imp = Importer(ledger.client, ledger.book_id, placeholder["id"])
        response = imp.preview(SIMPLE, imp.suggested(SIMPLE))
        assert response.status_code == 422 and "placeholder" in response.text

    def test_a_mapping_that_names_a_missing_column_is_a_422(self, imp: Importer):
        mapping = imp.suggested(SIMPLE) | {"amount": "Nope"}
        response = imp.preview(SIMPLE, mapping)
        assert response.status_code == 422 and "Nope" in response.text

    def test_an_amount_read_with_the_wrong_separator_is_refused(self, imp: Importer):
        mapping = imp.suggested(EUROPEAN) | {"decimal": "."}
        response = imp.preview(EUROPEAN, mapping)
        assert response.status_code == 422 and "separator" in response.text


class TestCommit:
    def test_it_posts_the_reviewed_rows_and_bumps_the_revision(self, imp: Importer):
        mapping = imp.suggested(SIMPLE)
        response = imp.commit(SIMPLE, mapping, decisions=_categorise(imp, SIMPLE))
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["posted"] == 3 and body["payees_created"] == 3 and body["ok"]
        assert body["revision_after"] > body["revision_before"]
        assert int(response.headers["X-PyMoney-Revision"]) == body["revision_after"]
        assert len(body["posted_transaction_ids"]) == 3

        imported = imp.client.get(
            f"/books/{imp.book_id}/accounts/{imp.account_id}/register",
            params={"start": "2026-02-01"},
        ).json()
        assert [row["quantity"]["minor"] for row in imported["items"]] == [
            -4211,
            250000,
            -380,
        ]
        first = body["posted_transaction_ids"][0]
        txn = imp.client.get(f"/books/{imp.book_id}/transactions/{first}").json()
        payees = {
            p["id"]: p["name"]
            for p in imp.client.get(f"/books/{imp.book_id}/payees").json()
        }
        assert payees[txn["payee_id"]] == "Tesco Stores 12 Leeds"
        # The dedup key is the transaction's imported_id: it is what a later
        # re-import recognises, and what an undo would search on.
        tesco_row = next(
            p
            for p in imp.preview(SIMPLE, mapping).json()["proposals"]
            if p["status"] == "duplicate" and p["existing_transaction_id"] == first
        )
        assert txn["imported_id"] == tesco_row["row_key"] != ""

    def test_an_unmatched_row_goes_to_the_fallback_account(self, ledger: Ledger):
        imp = Importer(ledger.client, ledger.book_id, ledger.checking)
        csv = b"Date,Details,Amount\n2026-02-03,MYSTERY,-15.00\n"
        body = imp.commit(csv, imp.suggested(csv), fallback=ledger.fuel).json()
        assert body["posted"] == 1 and body["uncategorised"] == 1
        fuel_balance = ledger.client.balance(ledger.fuel)
        assert fuel_balance["minor"] == 1500

    def test_an_unmatched_row_without_a_fallback_is_reported_not_fatal(
        self, imp: Importer
    ):
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-03,CATEGORISED,-5.00\n"
            b"2026-02-04,HOMELESS,-6.00\n"
        )
        mapping = imp.suggested(csv)
        rows = imp.preview(csv, mapping).json()["proposals"]
        decisions = [
            {
                "row_key": rows[0]["row_key"],
                "new_payee": "Shop",
                "category_id": imp_category(imp),
            }
        ]
        body = imp.commit(csv, mapping, decisions=decisions).json()
        assert body["posted"] == 1 and not body["ok"]
        assert body["problems"][0]["level"] == "ERROR"
        assert "uncategorised" in body["problems"][0]["message"]

    def test_reimporting_the_same_file_posts_nothing(self, imp: Importer):
        mapping = imp.suggested(SIMPLE)
        decisions = _categorise(imp, SIMPLE)
        imp.commit(SIMPLE, mapping, decisions=decisions)
        revision = int(imp.client.get(f"/books/{imp.book_id}").json()["revision"])
        second = imp.commit(SIMPLE, mapping, decisions=decisions).json()
        assert second["posted"] == 0 and second["skipped_duplicates"] == 3
        assert int(imp.client.get(f"/books/{imp.book_id}").json()["revision"]) == (
            revision
        )

    def test_defaults_skip_a_transfer_already_recorded(self, ledger: Ledger):
        imp = Importer(ledger.client, ledger.book_id, ledger.checking)
        ledger.client.post_transaction(
            "2026-02-10",
            [
                {"account_id": ledger.savings, "value": "300.00"},
                {"account_id": ledger.opening},
            ],
        )
        csv = (
            b"Date,Details,Amount\n"
            b"2026-02-11,MOVE TO SAVINGS,-300.00\n"
            b"2026-02-12,A REAL EXPENSE,-8.00\n"
        )
        opening = ledger.client.balance(ledger.checking)["minor"]
        body = imp.commit(csv, imp.suggested(csv), fallback=ledger.fuel).json()
        assert body["posted"] == 1 and body["skipped_transfers"] == 1
        # The transfer was not re-posted -- only the real expense moved the balance.
        assert ledger.client.balance(ledger.checking)["minor"] == opening - 800


class TestRememberedMapping:
    def test_a_commit_saves_the_mapping_it_used(self, imp: Importer):
        assert imp.get_mapping().json() is None
        mapping = imp.suggested(SIMPLE)
        imp.commit(SIMPLE, mapping, decisions=_categorise(imp, SIMPLE))
        saved = imp.get_mapping().json()
        assert saved["date"] == "Date" and saved["amount"] == "Amount"
        assert saved["description"] == ["Details"]

    def test_a_mapping_can_be_saved_without_importing(self, imp: Importer):
        mine = imp.suggested(EUROPEAN) | {"memo": ["Omschrijving"]}
        assert imp.put_mapping(mine).status_code == 200
        assert imp.get_mapping().json()["memo"] == ["Omschrijving"]

    def test_a_mapping_with_a_legacy_string_memo_still_loads(
        self, imp: Importer, ledger: Ledger
    ):
        # Rows written by the first version of the feature kept `memo` as one
        # string; the account's saved mapping must survive the upgrade.
        from uuid import UUID

        from pymoney.store import write_transaction
        from pymoney.store.import_mappings import save_mapping

        stored = imp.suggested(SIMPLE) | {"memo": "Details"}  # the old shape
        engine = ledger.client.http.app.state.engine
        with write_transaction(engine) as conn:
            save_mapping(conn, UUID(ledger.book_id), UUID(ledger.checking), stored)
        assert imp.get_mapping().json()["memo"] == ["Details"]

    def test_an_invalid_mapping_is_refused(self, imp: Importer):
        broken = imp.suggested(SIMPLE) | {"date": "", "amount": ""}
        assert imp.put_mapping(broken).status_code == 422

    def test_deleting_the_account_forgets_its_mapping(self, ledger: Ledger):
        spare = _account(ledger, "Spare", type_="CASH", parent_id=None)
        imp = Importer(ledger.client, ledger.book_id, spare["id"])
        imp.put_mapping(imp.suggested(SIMPLE))
        assert imp.get_mapping().json() is not None
        assert (
            ledger.client.delete(
                f"/books/{ledger.book_id}/accounts/{spare['id']}"
            ).status_code
            == 204
        )
        # A fresh account, same position, must not inherit the ghost mapping.
        fresh = _account(ledger, "Spare", type_="CASH", parent_id=None)
        assert (
            ledger.client.get(
                f"/books/{ledger.book_id}/accounts/{fresh['id']}/import-mapping"
            ).json()
            is None
        )


class TestForeignCurrency:
    """Importing a statement from an account kept in a foreign currency."""

    def _huf_book(self, api: Client) -> tuple[str, str, str, str]:
        book_id = api.make_book("Travel", code="EUR")
        huf = api.post(
            f"/books/{book_id}/commodities",
            json={"code": "HUF", "name": "Forint", "scale": 0},
        ).json()["id"]
        checking = api.account("HUF Account", "CHECKING", commodity_id=huf)
        groceries = api.account("Groceries", "EXPENSE")  # EUR
        misc = api.account("Uncategorised", "EXPENSE")  # EUR
        return book_id, checking["id"], groceries["id"], misc["id"]

    def test_the_transaction_is_in_the_account_currency_and_converted(self, api: Client):
        api.register("traveller@example.invalid")
        book_id, checking, groceries, _misc = self._huf_book(api)
        api.post(
            f"/books/{book_id}/prices",
            json={
                "base_id": _commodity(api, book_id, "EUR"),
                "quote_id": _commodity(api, book_id, "HUF"),
                "rate": "400",
                "effective_date": "2026-02-01",
            },
        )
        imp = Importer(api, book_id, checking)
        csv = b"Date,Details,Amount\n2026-02-03,SPAR BUDAPEST VII,-50000\n"
        mapping = imp.suggested(csv)
        rows = imp.preview(csv, mapping).json()["proposals"]
        assert rows[0]["amount"]["minor"] == -50000  # HUF, the bank's figure
        assert rows[0]["amount"]["commodity"] == "HUF"
        body = imp.commit(
            csv,
            mapping,
            decisions=[{"row_key": rows[0]["row_key"], "category_id": groceries}],
        ).json()
        assert body["posted"] == 1 and body["ok"]
        assert api.balance(checking)["text"] == "-50000"
        # 50000 HUF at 400 HUF/EUR = 125.00 EUR on the grocery account.
        assert api.balance(groceries)["minor"] == 12500

    def test_a_missing_rate_is_reported_not_fatal(self, api: Client):
        api.register("nofx@example.invalid")
        book_id, checking, groceries, _misc = self._huf_book(api)
        imp = Importer(api, book_id, checking)
        csv = b"Date,Details,Amount\n2026-02-03,SPAR,-50000\n"
        body = imp.commit(
            csv,
            imp.suggested(csv),
            fallback=groceries,  # the EUR uncategorised bucket
        ).json()
        assert body["posted"] == 0 and not body["ok"]
        assert "EUR/HUF price" in body["problems"][0]["message"]
        assert "2026-02-03" in body["problems"][0]["message"]
        # Its mapping was still remembered, so adding the price and re-importing
        # is a one-click job.
        assert imp.get_mapping().json()["date"] == "Date"


def _commodity(api: Client, book_id: str, code: str) -> str:
    found = api.get(f"/books/{book_id}/commodities").json()
    return next(c["id"] for c in found if c["code"] == code)


class TestDecisionContract:
    """``ImportDecisionIn`` is a JSON string inside a multipart form, so it is
    not in the OpenAPI document, so the frontend's ``ImportDecision`` type is
    hand-written. This is the tripwire for the two drifting apart."""

    def test_the_decision_shape_is_what_the_clients_expect(self):
        from pymoney.api.schemas import ImportDecisionIn

        assert set(ImportDecisionIn.model_fields) == {
            "row_key",
            "action",
            "payee_id",
            "new_payee",
            "category_id",
            "memo",
        }
        required = {
            name
            for name, field in ImportDecisionIn.model_fields.items()
            if field.is_required()
        }
        assert required == {"row_key"}
        # The action literal the wizards send.
        action = ImportDecisionIn.model_fields["action"].annotation
        assert getattr(action, "__args__", ()) == ("post", "skip")


class TestAuthorisation:
    def test_a_viewer_may_not_import(self, api: Client):
        api.register("keeper@example.invalid")
        book_id = api.make_book()
        account = api.account("Cash", "CASH")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        api.logout()
        api.register("guest@example.invalid")
        viewer = Importer(api, book_id, account["id"])
        assert viewer.inspect(SIMPLE).status_code == 403
        assert viewer.preview(SIMPLE, _anon_mapping()).status_code == 403
        assert viewer.commit(SIMPLE, _anon_mapping()).status_code == 403
        assert viewer.get_mapping().status_code == 403
        assert viewer.put_mapping(_anon_mapping()).status_code == 403

    def test_a_stranger_gets_a_404_not_a_403(self, api: Client):
        api.register("keeper@example.invalid")
        book_id = api.make_book()
        account = api.account("Cash", "CASH")
        api.logout()
        api.register("stranger@example.invalid")
        outsider = Importer(api, book_id, account["id"])
        assert outsider.inspect(SIMPLE).status_code == 404
        assert outsider.get_mapping().status_code == 404


# -- helpers ----------------------------------------------------------------
def _anon_mapping() -> dict:
    return {"date": "Date", "amount": "Amount", "description": ["Details"]}


def _account(ledger: Ledger, name: str, *, type_: str = "ASSET", parent_id=None):
    return ledger.client.account(name, type_, parent_id=parent_id)


def imp_category(imp: Importer) -> str:
    ledger_accounts = imp.client.get(f"/books/{imp.book_id}/accounts").json()
    return next(a["id"] for a in ledger_accounts if a["name"] == "Fuel")


def _categorise(imp: Importer, csv: bytes) -> list[dict]:
    """A decision per row: adopt the suggested new payee, file it under Fuel."""
    fuel = imp_category(imp)
    rows = imp.preview(csv, imp.suggested(csv)).json()["proposals"]
    decisions = []
    for row in rows:
        decision = {"row_key": row["row_key"], "category_id": fuel}
        if row["suggested_payee_id"]:
            decision["payee_id"] = row["suggested_payee_id"]
        else:
            decision["new_payee"] = row["suggested_payee_name"]
        decisions.append(decision)
    return decisions

"""Sync, idempotency, reports, and the OpenAPI contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest_api import Client, Ledger

SPEC = Path(__file__).parent.parent / "docs" / "openapi.json"


class TestRevisionHeader:
    def test_every_response_carries_it(self, ledger: Ledger):
        """So a client learns it is stale from any call, not only from /changes."""
        for url in (
            f"/books/{ledger.book_id}",
            f"/books/{ledger.book_id}/accounts",
            f"/books/{ledger.book_id}/commodities",
            f"/books/{ledger.book_id}/payees",
            f"/books/{ledger.book_id}/transactions",
            f"/books/{ledger.book_id}/prices",
        ):
            response = ledger.client.get(url)
            assert response.status_code == 200, url
            assert response.headers["X-PyMoney-Revision"].isdigit(), url

    def test_it_advances_on_a_write(self, ledger: Ledger):
        before = int(
            ledger.client.get(f"/books/{ledger.book_id}").headers["X-PyMoney-Revision"]
        )
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.fuel},
            ],
        )
        assert int(response.headers["X-PyMoney-Revision"]) == before + 1


class TestIdempotency:
    def test_a_replayed_key_does_not_double_post(self, ledger: Ledger):
        """The mobile-client-on-a-train case."""
        body = {
            "date": "2026-02-01",
            "legs": [
                {"account_id": ledger.checking, "value": "-42.50"},
                {"account_id": ledger.groceries},
            ],
        }
        headers = {"Idempotency-Key": "retry-me-42"}
        first = ledger.client.post(
            f"/books/{ledger.book_id}/transactions", json=body, headers=headers
        )
        second = ledger.client.post(
            f"/books/{ledger.book_id}/transactions", json=body, headers=headers
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert "Idempotency-Replayed" not in first.headers
        assert ledger.client.balance(ledger.groceries)["minor"] == 8420 + 4250

    def test_a_replay_does_not_burn_a_revision(self, ledger: Ledger):
        body = {
            "date": "2026-02-01",
            "legs": [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.fuel},
            ],
        }
        headers = {"Idempotency-Key": "once"}
        first = ledger.client.post(
            f"/books/{ledger.book_id}/transactions", json=body, headers=headers
        )
        second = ledger.client.post(
            f"/books/{ledger.book_id}/transactions", json=body, headers=headers
        )
        assert first.headers["X-PyMoney-Revision"] == second.headers["X-PyMoney-Revision"]

    def test_different_keys_post_twice(self, ledger: Ledger):
        body = {
            "date": "2026-02-01",
            "legs": [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.fuel},
            ],
        }
        for key in ("a", "b"):
            ledger.client.post(
                f"/books/{ledger.book_id}/transactions",
                json=body,
                headers={"Idempotency-Key": key},
            )
        assert ledger.client.balance(ledger.fuel)["minor"] == 200

    def test_no_key_means_no_deduplication(self, ledger: Ledger):
        body = {
            "date": "2026-02-01",
            "legs": [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.fuel},
            ],
        }
        for _ in range(2):
            ledger.client.post(f"/books/{ledger.book_id}/transactions", json=body)
        assert ledger.client.balance(ledger.fuel)["minor"] == 200

    def test_an_absurd_key_is_refused(self, ledger: Ledger):
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts",
            json={"name": "X", "type": "CASH"},
            headers={"Idempotency-Key": "k" * 300},
        )
        assert response.status_code == 400

    def test_it_works_for_accounts_too(self, ledger: Ledger):
        headers = {"Idempotency-Key": "make-one-account"}
        body = {"name": "Holiday", "type": "SAVINGS"}
        first = ledger.client.post(
            f"/books/{ledger.book_id}/accounts", json=body, headers=headers
        )
        second = ledger.client.post(
            f"/books/{ledger.book_id}/accounts", json=body, headers=headers
        )
        assert first.json()["id"] == second.json()["id"]
        names = [
            a["name"]
            for a in ledger.client.get(f"/books/{ledger.book_id}/accounts").json()
        ]
        assert names.count("Holiday") == 1


class TestChanges:
    def test_a_full_pull(self, ledger: Ledger):
        body = ledger.client.get(f"/books/{ledger.book_id}/changes").json()
        assert body["from_revision"] == 0
        assert body["complete"]
        assert body["to_revision"] == body["head"]
        kinds = {
            kind
            for batch in body["batches"]
            for kind in ("accounts", "transactions", "commodities")
            if batch[kind]
        }
        assert kinds == {"accounts", "transactions", "commodities"}

    def test_an_incremental_pull(self, ledger: Ledger):
        watermark = ledger.client.get(f"/books/{ledger.book_id}/changes").json()[
            "to_revision"
        ]
        ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-9.99"},
                {"account_id": ledger.fuel},
            ],
            memo="Later",
        )
        body = ledger.client.get(
            f"/books/{ledger.book_id}/changes", params={"since": watermark}
        ).json()
        assert len(body["batches"]) == 1
        assert body["batches"][0]["transactions"][0]["memo"] == "Later"
        assert body["batches"][0]["command"] == "PostTransaction"

    def test_a_caught_up_client_gets_nothing(self, ledger: Ledger):
        head = ledger.client.get(f"/books/{ledger.book_id}/changes").json()["head"]
        body = ledger.client.get(
            f"/books/{ledger.book_id}/changes", params={"since": head}
        ).json()
        assert body["batches"] == []
        assert body["complete"]

    def test_entities_arrive_in_typed_lists(self, ledger: Ledger):
        """So a client applies them table by table, not by sniffing a union."""
        body = ledger.client.get(f"/books/{ledger.book_id}/changes").json()
        batch = next(b for b in body["batches"] if b["transactions"])
        transaction = batch["transactions"][0]
        assert "splits" in transaction
        assert transaction["splits"][0]["value"]["minor"] != 0

    def test_the_actor_is_recorded(self, ledger: Ledger):
        body = ledger.client.get(f"/books/{ledger.book_id}/changes").json()
        # The audit trail names who did it, not just what happened.
        assert all(
            c["actor"] == "owner@example.invalid"
            for b in body["batches"]
            for c in b["changes"]
        )

    def test_a_deletion_is_a_tombstone(self, ledger: Ledger):
        posted = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.fuel},
            ],
        ).json()
        watermark = ledger.client.get(f"/books/{ledger.book_id}/changes").json()[
            "to_revision"
        ]
        ledger.client.delete(f"/books/{ledger.book_id}/transactions/{posted['id']}")
        body = ledger.client.get(
            f"/books/{ledger.book_id}/changes", params={"since": watermark}
        ).json()
        deletions = body["batches"][0]["deletions"]
        assert deletions == [{"kind": "TRANSACTION", "id": posted["id"]}]

    def test_a_revision_cap(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/changes", params={"limit_revisions": 2}
        ).json()
        assert len(body["batches"]) == 2
        assert not body["complete"]

    def test_the_row_cap_never_splits_a_revision(self, ledger: Ledger):
        """Half a merge is a state the server was never in."""
        ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.fuel}/merge",
            json={"source_id": ledger.groceries},
        )
        head = ledger.client.get(f"/books/{ledger.book_id}").headers["X-PyMoney-Revision"]
        body = ledger.client.get(
            f"/books/{ledger.book_id}/changes",
            params={"since": int(head) - 1, "limit_rows": 1},
        ).json()
        batch = body["batches"][0]
        assert len(batch["transactions"]) + len(batch["deletions"]) > 1

    def test_paging_to_completion_sees_every_revision_once(self, ledger: Ledger):
        seen: list[int] = []
        since = 0
        for _ in range(50):
            body = ledger.client.get(
                f"/books/{ledger.book_id}/changes",
                params={"since": since, "limit_revisions": 2},
            ).json()
            seen.extend(b["revision"] for b in body["batches"])
            since = body["to_revision"]
            if body["complete"]:
                break
        assert seen == sorted(seen)
        assert len(seen) == len(set(seen))

    def test_one_revision_on_its_own(self, ledger: Ledger):
        body = ledger.client.get(f"/books/{ledger.book_id}/changes/1").json()
        assert body["revision"] == 1
        assert body["command"] == "InitialiseBook"

    def test_a_watermark_ahead_of_the_book_is_refused(self, ledger: Ledger):
        """The client is on the wrong book, or we were restored from a backup."""
        response = ledger.client.get(
            f"/books/{ledger.book_id}/changes", params={"since": 9999}
        )
        assert response.status_code == 422
        assert "ahead of this book" in response.json()["error"]["message"]

    def test_a_replica_can_be_rebuilt_from_changes_alone(self, ledger: Ledger):
        """End to end: apply whole batches and compare against the server."""
        replica: dict[str, dict] = {}
        since = 0
        while True:
            body = ledger.client.get(
                f"/books/{ledger.book_id}/changes",
                params={"since": since, "limit_revisions": 3},
            ).json()
            for batch in body["batches"]:
                for table in (
                    "commodities",
                    "accounts",
                    "payees",
                    "transactions",
                    "prices",
                ):
                    for entity in batch[table]:
                        replica[entity["id"]] = entity
                for tombstone in batch["deletions"]:
                    replica.pop(tombstone["id"], None)
            since = body["to_revision"]
            if body["complete"]:
                break

        server_accounts = ledger.client.get(f"/books/{ledger.book_id}/accounts").json()
        for account in server_accounts:
            assert replica[account["id"]]["path"] == account["path"]
        server_transactions = ledger.client.get(
            f"/books/{ledger.book_id}/transactions"
        ).json()["items"]
        for transaction in server_transactions:
            assert replica[transaction["id"]]["splits"] == transaction["splits"]

    def test_integrity_is_exposed(self, ledger: Ledger):
        assert ledger.client.get(f"/books/{ledger.book_id}/integrity").json() == []


class TestReports:
    def test_balance_sheet(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={"as_of": "2026-01-31"},
        ).json()
        assert body["assets"]["minor"] == 411580
        assert body["net_worth"]["minor"] == 411580
        assert body["target"] == "EUR"
        paths = {row["path"] for row in body["rows"]}
        assert "Assets:Checking" in paths

    def test_liabilities_are_shown_both_ways(self, ledger: Ledger):
        card = ledger.client.account("Visa", "CREDIT_CARD")
        ledger.client.post_transaction(
            "2026-01-10",
            [
                {"account_id": card["id"], "value": "-61.50"},
                {"account_id": ledger.fuel},
            ],
        )
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={"as_of": "2026-01-31"},
        ).json()
        # Stored signed, reported positive: both are in the payload so a client
        # never has to guess the convention.
        assert body["liabilities"]["minor"] == -6150
        assert body["liabilities_owed"]["minor"] == 6150

    def test_income_statement(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/income-statement",
            params={"start": "2026-01-01", "end": "2026-01-31"},
        ).json()
        assert body["income_earned"]["minor"] == 320000
        assert body["expenses"]["minor"] == 8420
        assert body["net"]["minor"] == 320000 - 8420
        assert body["savings_rate"]["num"] > 0

    def test_a_period_excludes_what_is_outside_it(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/income-statement",
            params={"start": "2026-01-10", "end": "2026-01-31"},
        ).json()
        assert body["expenses"]["minor"] == 0

    def test_an_unpriced_commodity_is_fatal_by_default(self, ledger: Ledger):
        """Valuing a holding at zero would be worse than an error."""
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        wallet = ledger.client.account("Wallet", "CASH", commodity_id=usd["id"])
        equity = ledger.client.account("Open USD", "EQUITY", commodity_id=usd["id"])
        ledger.client.post_transaction(
            "2026-01-20",
            [
                {"account_id": wallet["id"], "value": "100.00"},
                {"account_id": equity["id"], "value": "-100.00"},
            ],
            currency_id=usd["id"],
        )
        strict = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={"as_of": "2026-01-31"},
        )
        assert strict.status_code == 422
        assert strict.json()["error"]["type"] == "NoPriceError"

        lenient = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={"as_of": "2026-01-31", "strict": False},
        ).json()
        assert lenient["unpriced"] == ["USD"]

    def test_reports_show_the_rates_they_used(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        wallet = ledger.client.account("Wallet", "CASH", commodity_id=usd["id"])
        equity = ledger.client.account("Open USD", "EQUITY", commodity_id=usd["id"])
        ledger.client.post_transaction(
            "2026-01-20",
            [
                {"account_id": wallet["id"], "value": "100.00"},
                {"account_id": equity["id"], "value": "-100.00"},
            ],
            currency_id=usd["id"],
        )
        ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": "0.9",
                "effective_date": "2026-01-31",
                "source": "ecb",
            },
        )
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={"as_of": "2026-01-31"},
        ).json()
        assert body["assets"]["minor"] == 411580 + 9000
        conversion = body["conversions"][0]
        assert conversion["base"] == "USD"
        assert conversion["rate"]["num"] == 9
        assert conversion["sources"] == ["ecb"]

    def test_a_knowledge_cutoff_reproduces_an_old_report(self, ledger: Ledger):
        """Decision #12: the same policy always gives the same numbers."""
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        wallet = ledger.client.account("Wallet", "CASH", commodity_id=usd["id"])
        equity = ledger.client.account("Open USD", "EQUITY", commodity_id=usd["id"])
        ledger.client.post_transaction(
            "2026-01-20",
            [
                {"account_id": wallet["id"], "value": "100.00"},
                {"account_id": equity["id"], "value": "-100.00"},
            ],
            currency_id=usd["id"],
        )
        for rate, as_of in (
            ("0.90", "2026-02-01T00:00:00Z"),
            ("0.95", "2026-03-01T00:00:00Z"),
        ):
            ledger.client.post(
                f"/books/{ledger.book_id}/prices",
                json={
                    "base_id": usd["id"],
                    "quote_id": ledger.eur,
                    "rate": rate,
                    "effective_date": "2026-01-31",
                    "as_of": as_of,
                },
            )
        params = {"as_of": "2026-01-31"}
        latest = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet", params=params
        ).json()
        earlier = ledger.client.get(
            f"/books/{ledger.book_id}/reports/balance-sheet",
            params={**params, "known_before": "2026-02-15T00:00:00Z"},
        ).json()
        assert latest["assets"]["minor"] == 411580 + 9500
        assert earlier["assets"]["minor"] == 411580 + 9000

    def test_holdings(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        aapl = ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "AAPL", "kind": "STOCK", "scale": 4},
        ).json()
        brokerage = ledger.client.account(
            "Brokerage", "INVESTMENT", commodity_id=usd["id"]
        )
        shares = ledger.client.account(
            "AAPL", "STOCK", parent_id=brokerage["id"], commodity_id=aapl["id"]
        )
        ledger.client.post_transaction(
            "2026-01-15",
            [
                {"account_id": shares["id"], "quantity": "10", "price": "180.00"},
                {"account_id": brokerage["id"]},
            ],
            currency_id=usd["id"],
        )
        ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": aapl["id"],
                "quote_id": usd["id"],
                "rate": "190.50",
                "effective_date": "2026-01-31",
            },
        )
        body = ledger.client.get(
            f"/books/{ledger.book_id}/reports/holdings",
            params={"as_of": "2026-01-31", "target_id": usd["id"]},
        ).json()
        position = body["positions"][0]
        assert position["quantity"]["minor"] == 100000
        assert position["market_value"]["minor"] == 190500
        assert position["cost"]["minor"] == 180000
        assert position["unrealised_gain"]["minor"] == 10500


class TestOpenApi:
    """The document is part of the contract, so it is committed and checked."""

    def test_it_is_committed(self, api: Client):
        assert SPEC.exists(), f"{SPEC} is missing; run `make openapi` to generate it"

    def test_it_matches_the_code(self, api: Client):
        """The drift test. A changed endpoint must come with a regenerated spec,
        so a client generated from the committed document is never wrong."""
        current = json.loads(json.dumps(api.http.app.openapi(), sort_keys=True))
        committed = json.loads(SPEC.read_text())
        assert current == committed, (
            "the OpenAPI document is out of date; run `make openapi`"
        )

    def test_it_describes_the_exactness_rule(self, api: Client):
        """The one thing a client integrator must not miss."""
        spec = api.http.app.openapi()
        assert "never JSON floats" in spec["info"]["description"].replace("\n", " ")

    def test_every_route_is_tagged_and_summarised(self, api: Client):
        spec = api.http.app.openapi()
        for path, methods in spec["paths"].items():
            for method, operation in methods.items():
                assert operation.get("tags"), f"{method} {path} has no tag"
                assert operation.get("summary"), f"{method} {path} has no summary"

    def test_error_responses_are_documented(self, api: Client):
        spec = api.http.app.openapi()
        operation = spec["paths"]["/books/{book_id}/transactions"]["post"]
        assert "409" in operation["responses"]
        assert "422" in operation["responses"]

    def test_amounts_are_described_as_exact_minor_units(self, api: Client):
        spec = api.http.app.openapi()
        amount = spec["components"]["schemas"]["AmountOut"]
        assert "Authoritative" in amount["properties"]["minor"]["description"]
        assert amount["properties"]["minor"]["type"] == "integer"

    def test_reconcile_state_is_a_name_not_a_number(self, api: Client):
        spec = api.http.app.openapi()
        enum = spec["components"]["schemas"]["ReconcileStateName"]["enum"]
        assert "RECONCILED" in enum

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/auth/login",
            "/books",
            "/books/{book_id}/accounts",
            "/books/{book_id}/transactions",
            "/books/{book_id}/prices",
            "/books/{book_id}/changes",
            "/books/{book_id}/reports/balance-sheet",
        ],
    )
    def test_the_documented_surface_is_present(self, api: Client, path: str):
        assert path in api.http.app.openapi()["paths"]


class TestHealth:
    def test_needs_no_authentication(self, api: Client):
        """A load balancer has no session."""
        body = api.http.get("/health").json()
        assert body["status"] == "ok"
        assert body["books"] == 0

    def test_counts_books(self, ledger: Ledger):
        assert ledger.client.get("/health").json()["books"] == 1

    def test_never_leaks_credentials(self, api: Client):
        assert "secret" not in api.http.get("/health").json()["database"]


class TestInMemoryIsRefused:
    def test_with_an_explanation(self):
        """It is a single connection; the API needs two. Better to say so now."""
        from pymoney.api import create_app
        from pymoney.api.settings import Settings

        with pytest.raises(ValueError, match="only one connection"):
            create_app(settings=Settings(database_url="sqlite+pysqlite:///:memory:"))

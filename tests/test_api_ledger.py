"""The ledger over HTTP: commodities, accounts, payees, transactions, registers.

The theme running through these is that the wire format must not lose anything.
Amounts go out as exact minor units, rates as exact ratios, and a JSON float is
refused rather than rounded.
"""

from __future__ import annotations

from tests.conftest_api import Client, Ledger


class TestExactNumbers:
    """The single most important property of this API."""

    def test_a_json_float_is_refused(self, ledger: Ledger):
        """0.1 is not one tenth. Accepting it would be the end of the ledger."""
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": 10.5},
                {"account_id": ledger.groceries},
            ],
        )
        assert response.status_code == 422
        assert "not a JSON number" in response.text

    def test_a_string_amount_is_exact(self, ledger: Ledger):
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-0.07"},
                {"account_id": ledger.groceries},
            ],
        )
        assert response.status_code == 201
        assert response.json()["splits"][0]["value"]["minor"] == -7

    def test_an_integer_amount_is_accepted(self, ledger: Ledger):
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": -5},
                {"account_id": ledger.groceries},
            ],
        )
        assert response.json()["splits"][0]["value"]["minor"] == -500

    def test_a_rational_amount_is_accepted(self, ledger: Ledger):
        """A third of a euro is a perfectly good input; the server decides how it
        books."""
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries, "value": "10/3"},
                {"account_id": ledger.fuel, "value": "10/3"},
                {"account_id": ledger.fuel, "value": "10/3"},
            ],
        )
        assert response.status_code == 201
        values = sorted(s["value"]["minor"] for s in response.json()["splits"])
        assert values == [-1000, 333, 333, 334]
        assert sum(values) == 0

    def test_amounts_carry_everything_a_client_needs(self, ledger: Ledger):
        amount = ledger.client.balance(ledger.checking)
        assert amount["minor"] == 411580
        assert amount["scale"] == 100
        assert amount["commodity"] == "EUR"
        assert amount["text"] == "4115.80"

    def test_a_zero_decimal_currency(self, user: Client):
        user.make_book("Yen book", code="JPY", scale=0)
        response = user.post(
            f"/books/{user.book_id}/commodities",
            json={"code": "JPY2", "name": "Test", "scale": 0},
        )
        assert response.json()["minor_unit_factor"] == 1
        cash = user.account("Cash", "CASH")
        equity = user.account("Open", "EQUITY")
        posted = user.post_transaction(
            "2026-01-01",
            [
                {"account_id": cash["id"], "value": "500000"},
                {"account_id": equity["id"]},
            ],
        )
        amount = posted.json()["splits"][0]["value"]
        assert amount["minor"] == 500000
        assert amount["scale"] == 1
        assert amount["text"] == "500000"


class TestCommodities:
    def test_the_base_currency_exists(self, ledger: Ledger):
        found = ledger.client.get(f"/books/{ledger.book_id}/commodities").json()
        assert [c["code"] for c in found] == ["EUR"]
        assert found[0]["minor_unit_factor"] == 100

    def test_add_a_security(self, ledger: Ledger):
        response = ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={
                "code": "AAPL",
                "name": "Apple Inc.",
                "kind": "STOCK",
                "scale": 4,
                "isin": "US0378331005",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["minor_unit_factor"] == 10000
        assert body["kind"] == "STOCK"
        assert body["isin"] == "US0378331005"

    def test_a_non_decimal_minor_unit(self, ledger: Ledger):
        """The ouguiya divides into five khoums, not a hundred cents."""
        response = ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "MRU", "minor_unit_factor": 5},
        )
        assert response.json()["minor_unit_factor"] == 5

    def test_filter_by_kind(self, ledger: Ledger):
        ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "AAPL", "kind": "STOCK", "scale": 4},
        )
        found = ledger.client.get(
            f"/books/{ledger.book_id}/commodities", params={"kind": "STOCK"}
        ).json()
        assert [c["code"] for c in found] == ["AAPL"]

    def test_a_duplicate_code_is_a_conflict(self, ledger: Ledger):
        response = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "eur"}
        )
        assert response.status_code == 409
        assert response.json()["error"]["type"] == "ConflictError"

    def test_the_base_currency_cannot_be_deleted(self, ledger: Ledger):
        response = ledger.client.delete(
            f"/books/{ledger.book_id}/commodities/{ledger.eur}"
        )
        assert response.status_code == 422


class TestAccounts:
    def test_paths_are_derived(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        ).json()
        assert body["path"] == "Assets:Checking"
        assert body["account_class"] == "ASSET"

    def test_a_child_must_match_the_parent_class(self, ledger: Ledger):
        assets = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        ).json()["parent_id"]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts",
            json={"name": "Wrong", "type": "EXPENSE", "parent_id": assets},
        )
        assert response.status_code == 422

    def test_rename_keeps_the_history(self, ledger: Ledger):
        before = ledger.client.balance(ledger.checking)
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/rename",
            json={"name": "Current Account"},
        )
        assert response.json()["path"] == "Assets:Current Account"
        assert ledger.client.balance(ledger.checking) == before

    def test_move_to_the_top_level(self, ledger: Ledger):
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/move",
            json={"parent_id": None},
        )
        assert response.json()["path"] == "Checking"

    def test_a_move_under_a_descendant_is_refused(self, ledger: Ledger):
        parent = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        ).json()["parent_id"]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{parent}/move",
            json={"parent_id": ledger.checking},
        )
        assert response.status_code == 422
        assert "descendant" in response.json()["error"]["message"]

    def test_close_and_reopen(self, ledger: Ledger):
        url = f"/books/{ledger.book_id}/accounts/{ledger.savings}/closed"
        assert ledger.client.post(url, json={"closed": True}).json()["closed"]
        assert not ledger.client.post(url, json={"closed": False}).json()["closed"]

    def test_a_closed_account_rejects_postings(self, ledger: Ledger):
        ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.savings}/closed",
            json={"closed": True},
        )
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.savings, "value": "10"},
                {"account_id": ledger.salary},
            ],
        )
        assert response.status_code == 422
        assert "closed" in response.json()["error"]["message"]

    def test_patch_metadata(self, ledger: Ledger):
        response = ledger.client.patch(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}",
            json={"description": "Everyday", "iban": "DE02120300000000202051"},
        )
        body = response.json()
        assert body["description"] == "Everyday"
        assert body["iban"] == "DE02120300000000202051"
        assert body["code"] == ""

    def test_an_unknown_field_is_an_error(self, ledger: Ledger):
        """A typo should not be silently ignored."""
        response = ledger.client.patch(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}",
            json={"descriptionn": "typo"},
        )
        assert response.status_code == 422

    def test_merge(self, ledger: Ledger):
        before = ledger.client.balance(ledger.groceries)["minor"]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.fuel}/merge",
            json={"source_id": ledger.groceries},
        )
        assert response.status_code == 200
        assert ledger.client.balance(ledger.fuel)["minor"] == before
        assert (
            ledger.client.get(
                f"/books/{ledger.book_id}/accounts/{ledger.groceries}"
            ).status_code
            == 404
        )

    def test_an_account_with_postings_cannot_be_deleted(self, ledger: Ledger):
        response = ledger.client.delete(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        )
        assert response.status_code == 409
        assert "merge" in response.json()["error"]["message"]

    def test_commodity_cannot_change_once_money_is_posted(self, ledger: Ledger):
        response = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        )
        usd = response.json()["id"]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/commodity",
            json={"commodity_id": usd},
        )
        assert response.status_code == 409
        assert "reinterpret" in response.json()["error"]["message"]
        # ...but an unused account can be corrected.
        assert (
            ledger.client.post(
                f"/books/{ledger.book_id}/accounts/{ledger.savings}/commodity",
                json={"commodity_id": usd},
            ).status_code
            == 200
        )

    def test_balance_as_of_a_date(self, ledger: Ledger):
        assert (
            ledger.client.balance(ledger.checking, as_of="2026-01-01")["minor"] == 100000
        )
        assert ledger.client.balance(ledger.checking, as_of="2025-12-31")["minor"] == 0

    def test_subtree_balance(self, ledger: Ledger):
        parent = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        ).json()["parent_id"]
        body = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{parent}/balance"
        ).json()
        assert body["balance"]["minor"] == 0
        assert [a["minor"] for a in body["subtree"]] == [411580]


class TestPayees:
    def test_create_and_attach(self, ledger: Ledger):
        payee = ledger.client.post(
            f"/books/{ledger.book_id}/payees",
            json={"name": "Supermarket", "match_keys": ["SUPERMKT"]},
        ).json()
        assert payee["match_keys"] == ["SUPERMKT"]
        posted = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-12.00"},
                {"account_id": ledger.groceries},
            ],
            payee_id=payee["id"],
        )
        assert posted.json()["payee_id"] == payee["id"]

    def test_search(self, ledger: Ledger):
        for name in ("Amazon", "Amazon EU", "Butcher"):
            ledger.client.post(f"/books/{ledger.book_id}/payees", json={"name": name})
        found = ledger.client.get(
            f"/books/{ledger.book_id}/payees", params={"search": "amazon"}
        ).json()
        assert {p["name"] for p in found} == {"Amazon", "Amazon EU"}

    def test_merge_is_one_revision(self, ledger: Ledger):
        """400 transactions repointed is one sync step, not 400 (decision #13)."""
        target = ledger.client.post(
            f"/books/{ledger.book_id}/payees", json={"name": "Amazon"}
        ).json()
        source = ledger.client.post(
            f"/books/{ledger.book_id}/payees", json={"name": "AMZN"}
        ).json()
        ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-5.00"},
                {"account_id": ledger.groceries},
            ],
            payee_id=source["id"],
        )
        before = int(
            ledger.client.get(f"/books/{ledger.book_id}").headers["X-PyMoney-Revision"]
        )
        response = ledger.client.post(
            f"/books/{ledger.book_id}/payees/{target['id']}/merge",
            json={"source_ids": [source["id"]]},
        )
        assert response.status_code == 200
        assert int(response.headers["X-PyMoney-Revision"]) == before + 1
        assert "AMZN" in response.json()["match_keys"]

    def test_patch(self, ledger: Ledger):
        payee = ledger.client.post(
            f"/books/{ledger.book_id}/payees", json={"name": "Shop"}
        ).json()
        response = ledger.client.patch(
            f"/books/{ledger.book_id}/payees/{payee['id']}",
            json={"notes": "round the corner"},
        )
        assert response.json()["notes"] == "round the corner"
        assert response.json()["name"] == "Shop"


class TestTransactions:
    def test_a_residual_leg_balances_the_entry(self, ledger: Ledger):
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-100.00"},
                {"account_id": ledger.groceries, "value": "60.00"},
                {"account_id": ledger.fuel},
            ],
        )
        assert [s["value"]["minor"] for s in response.json()["splits"]] == [
            -10000,
            6000,
            4000,
        ]

    def test_an_unbalanced_entry_is_refused(self, ledger: Ledger):
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries, "value": "9.00"},
            ],
        )
        assert response.status_code == 422
        assert response.json()["error"]["type"] == "UnbalancedTransactionError"

    def test_an_imbalance_account_can_absorb_it(self, ledger: Ledger):
        imbalance = ledger.client.account("Imbalance", "EQUITY")
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries, "value": "9.00"},
            ],
            imbalance_account_id=imbalance["id"],
        )
        assert response.status_code == 201
        assert ledger.client.balance(imbalance["id"])["minor"] == 100

    def test_multi_currency(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        wallet = ledger.client.account("Wallet", "CASH", commodity_id=usd["id"])
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-91.74"},
                {
                    "account_id": wallet["id"],
                    "value": "91.74",
                    "price": "0.9174",
                },
            ],
        )
        assert response.status_code == 201
        legs = {s["account_id"]: s for s in response.json()["splits"]}
        usd_leg = legs[wallet["id"]]
        assert usd_leg["quantity"]["minor"] == 10000
        assert usd_leg["quantity"]["commodity"] == "USD"
        assert usd_leg["value"]["minor"] == 9174
        assert usd_leg["price"] == {"num": 4587, "den": 5000, "approx": "0.9174000000"}

    def test_the_implied_price_is_exposed(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        wallet = ledger.client.account("Wallet", "CASH", commodity_id=usd["id"])
        response = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-91.74"},
                {"account_id": wallet["id"], "value": "91.74", "quantity": "100.00"},
            ],
        )
        leg = next(
            s for s in response.json()["splits"] if s["account_id"] == wallet["id"]
        )
        assert leg["price"] is None
        assert leg["implied_price"]["num"] == 4587
        assert leg["implied_price"]["den"] == 5000

    def test_update_replaces_the_legs(self, ledger: Ledger):
        posted = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries},
            ],
        ).json()
        response = ledger.client.patch(
            f"/books/{ledger.book_id}/transactions/{posted['id']}",
            json={
                "memo": "corrected",
                "legs": [
                    {"account_id": ledger.checking, "value": "-20.00"},
                    {"account_id": ledger.fuel},
                ],
            },
        )
        assert response.json()["memo"] == "corrected"
        assert ledger.client.balance(ledger.fuel)["minor"] == 2000
        assert ledger.client.balance(ledger.groceries)["minor"] == 8420

    def test_reconciled_transactions_are_locked(self, ledger: Ledger):
        posted = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries},
            ],
        ).json()
        split = posted["splits"][0]["id"]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/transactions/{posted['id']}"
            f"/splits/{split}/reconcile",
            json={"state": "RECONCILED", "on": "2026-02-28"},
        )
        assert response.json()["locked"] is True
        assert response.json()["reconcile_state"] == "RECONCILED"
        blocked = ledger.client.patch(
            f"/books/{ledger.book_id}/transactions/{posted['id']}",
            json={"memo": "nope"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["type"] == "ImmutableError"

    def test_reconcile_a_batch(self, ledger: Ledger):
        register = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register"
        ).json()
        split_ids = [row["split_id"] for row in register["items"]]
        response = ledger.client.post(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/reconcile",
            json={
                "split_ids": split_ids,
                "state": "RECONCILED",
                "statement_date": "2026-01-31",
            },
        )
        assert response.status_code == 204
        again = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register"
        ).json()
        assert all(r["reconcile_state"] == "RECONCILED" for r in again["items"])

    def test_delete(self, ledger: Ledger):
        posted = ledger.client.post_transaction(
            "2026-02-01",
            [
                {"account_id": ledger.checking, "value": "-10.00"},
                {"account_id": ledger.groceries},
            ],
        ).json()
        assert (
            ledger.client.delete(
                f"/books/{ledger.book_id}/transactions/{posted['id']}"
            ).status_code
            == 204
        )
        assert (
            ledger.client.get(
                f"/books/{ledger.book_id}/transactions/{posted['id']}"
            ).status_code
            == 404
        )

    def test_a_duplicate_imported_id_is_a_conflict(self, ledger: Ledger):
        legs = [
            {"account_id": ledger.checking, "value": "-10.00"},
            {"account_id": ledger.groceries},
        ]
        assert (
            ledger.client.post_transaction(
                "2026-02-01", legs, imported_id="OFX-1"
            ).status_code
            == 201
        )
        assert (
            ledger.client.post_transaction(
                "2026-02-02", legs, imported_id="OFX-1"
            ).status_code
            == 409
        )

    def test_filter_and_paginate(self, ledger: Ledger):
        for day in range(1, 11):
            ledger.client.post_transaction(
                f"2026-03-{day:02d}",
                [
                    {"account_id": ledger.checking, "value": f"-{day}.00"},
                    {"account_id": ledger.fuel},
                ],
            )
        page = ledger.client.get(
            f"/books/{ledger.book_id}/transactions",
            params={"account_id": ledger.fuel, "limit": 4},
        ).json()
        assert len(page["items"]) == 4
        assert page["has_more"]
        seen = [t["id"] for t in page["items"]]
        while page["has_more"]:
            page = ledger.client.get(
                f"/books/{ledger.book_id}/transactions",
                params={
                    "account_id": ledger.fuel,
                    "limit": 4,
                    "cursor": page["next_cursor"],
                },
            ).json()
            seen.extend(t["id"] for t in page["items"])
        assert len(seen) == 10
        assert len(set(seen)) == 10

    def test_filter_by_date_range(self, ledger: Ledger):
        page = ledger.client.get(
            f"/books/{ledger.book_id}/transactions",
            params={"start": "2026-01-05", "end": "2026-01-05"},
        ).json()
        assert [t["memo"] for t in page["items"]] == ["Supermarket"]

    def test_a_malformed_cursor_is_a_clear_error(self, ledger: Ledger):
        response = ledger.client.get(
            f"/books/{ledger.book_id}/transactions", params={"cursor": "!!!not base64"}
        )
        assert response.status_code == 400
        assert "cursor" in response.json()["detail"]


class TestRegister:
    def test_rows_and_running_balance(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register"
        ).json()
        rows = body["items"]
        assert [r["quantity"]["minor"] for r in rows] == [100000, -8420, 320000]
        assert [r["running_balance"]["minor"] for r in rows] == [
            100000,
            91580,
            411580,
        ]
        assert body["totals"]["opening"]["minor"] == 0
        assert body["totals"]["closing"]["minor"] == 411580

    def test_memo_falls_back_to_the_transaction(self, ledger: Ledger):
        rows = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register"
        ).json()["items"]
        assert rows[0]["memo"] == "Opening balance"

    def test_a_window_has_an_opening_balance(self, ledger: Ledger):
        body = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register",
            params={"start": "2026-01-05", "end": "2026-01-05"},
        ).json()
        assert len(body["items"]) == 1
        assert body["totals"]["opening"]["minor"] == 100000
        assert body["items"][0]["running_balance"]["minor"] == 91580

    def test_keyset_pagination_covers_every_row_once(self, ledger: Ledger):
        for day in range(1, 16):
            ledger.client.post_transaction(
                f"2026-04-{day:02d}",
                [
                    {"account_id": ledger.checking, "value": "-1.00"},
                    {"account_id": ledger.fuel},
                ],
            )
        seen: list[str] = []
        cursor = None
        for _ in range(20):
            params = {"limit": 5}
            if cursor:
                params["cursor"] = cursor
            page = ledger.client.get(
                f"/books/{ledger.book_id}/accounts/{ledger.checking}/register",
                params=params,
            ).json()
            seen.extend(r["split_id"] for r in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert len(seen) == 18
        assert len(set(seen)) == 18

    def test_include_children(self, ledger: Ledger):
        parent = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}"
        ).json()["parent_id"]
        body = ledger.client.get(
            f"/books/{ledger.book_id}/accounts/{parent}/register",
            params={"include_children": True},
        ).json()
        assert len(body["items"]) == 3
        # Totals are per-account, so a subtree view does not claim one.
        assert body["totals"]["opening"] is None


class TestPrices:
    def test_record_and_list(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        response = ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": "0.9174",
                "effective_date": "2026-01-31",
                "source": "ecb",
            },
        )
        assert response.status_code == 201
        assert response.json()["rate"] == {
            "num": 4587,
            "den": 5000,
            "approx": "0.9174000000",
        }
        page = ledger.client.get(f"/books/{ledger.book_id}/prices").json()
        assert len(page["items"]) == 1

    def test_a_ratio_rate_stays_exact(self, ledger: Ledger):
        """10/9 is not 1.111; the wire format must not round it."""
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        response = ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": "10/9",
                "effective_date": "2026-01-31",
            },
        )
        assert response.json()["rate"]["num"] == 10
        assert response.json()["rate"]["den"] == 9

    def test_a_correction_is_a_new_row(self, ledger: Ledger):
        """Bitemporal: the old figure is never destroyed (decision #9)."""
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        for rate in ("0.91", "0.92"):
            ledger.client.post(
                f"/books/{ledger.book_id}/prices",
                json={
                    "base_id": usd["id"],
                    "quote_id": ledger.eur,
                    "rate": rate,
                    "effective_date": "2026-01-31",
                },
            )
        page = ledger.client.get(f"/books/{ledger.book_id}/prices").json()
        assert len(page["items"]) == 2

    def test_filter_by_knowledge_time(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": "0.91",
                "effective_date": "2026-01-31",
                "as_of": "2026-02-01T12:00:00Z",
            },
        )
        early = ledger.client.get(
            f"/books/{ledger.book_id}/prices",
            params={"known_before": "2026-01-01T00:00:00Z"},
        ).json()
        assert early["items"] == []

    def test_convert_shows_its_working(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        aapl = ledger.client.post(
            f"/books/{ledger.book_id}/commodities",
            json={"code": "AAPL", "kind": "STOCK", "scale": 4},
        ).json()
        for base, quote, rate in (
            (aapl["id"], usd["id"], "190.50"),
            (usd["id"], ledger.eur, "0.9"),
        ):
            ledger.client.post(
                f"/books/{ledger.book_id}/prices",
                json={
                    "base_id": base,
                    "quote_id": quote,
                    "rate": rate,
                    "effective_date": "2026-01-31",
                },
            )
        body = ledger.client.get(
            f"/books/{ledger.book_id}/prices/convert",
            params={
                "base_id": aapl["id"],
                "quote_id": ledger.eur,
                "as_of": "2026-01-31",
            },
        ).json()
        assert body["hops"] == 2
        assert body["path"] == ["AAPL", "USD", "EUR"]
        assert body["rate"]["approx"].startswith("171.45")

    def test_no_price_is_a_clear_422(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        response = ledger.client.get(
            f"/books/{ledger.book_id}/prices/convert",
            params={"base_id": usd["id"], "quote_id": ledger.eur},
        )
        assert response.status_code == 422
        assert response.json()["error"]["type"] == "NoPriceError"

    def test_an_unstorable_rate_is_refused_not_truncated(self, ledger: Ledger):
        usd = ledger.client.post(
            f"/books/{ledger.book_id}/commodities", json={"code": "USD"}
        ).json()
        response = ledger.client.post(
            f"/books/{ledger.book_id}/prices",
            json={
                "base_id": usd["id"],
                "quote_id": ledger.eur,
                "rate": f"{2**70 + 1}/3",
                "effective_date": "2026-01-31",
            },
        )
        assert response.status_code == 422
        assert "64-bit" in response.text

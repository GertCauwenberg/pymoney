"""Scheduled / recurring transactions over HTTP: CRUD, the due panel, and
posting an occurrence on confirmation. The domain is exercised in
``test_schedules``; this pins the wire contract and the auth."""

from __future__ import annotations

from tests.conftest_api import Client, Ledger


def _schedule_body(ledger: Ledger, **over) -> dict:
    body = {
        "name": "Rent",
        "recurrence": {"frequency": "MONTHLY", "start": "2026-01-01"},
        "template": {
            "legs": [
                {"account_id": ledger.checking, "value": "-1200.00"},
                {"account_id": ledger.fuel},
            ],
            "memo": "Monthly rent",
        },
    }
    body.update(over)
    return body


class TestScheduleCrud:
    def test_create_list_update_delete(self, ledger: Ledger):
        c = ledger.client
        created = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        )
        assert created.status_code == 201
        s = created.json()
        assert s["recurrence_text"] == "every month on day 1"
        assert s["next_due"] == "2026-01-01" and s["occurrences_done"] == 0
        assert s["upcoming"][:2] == ["2026-01-01", "2026-02-01"]
        assert s["template"]["legs"][1]["is_residual"] is True

        listed = c.get(f"/books/{ledger.book_id}/schedules").json()
        assert [x["name"] for x in listed] == ["Rent"]

        moved = c.patch(
            f"/books/{ledger.book_id}/schedules/{s['id']}",
            json={
                "recurrence": {"frequency": "MONTHLY", "start": "2026-06-15"},
                "name": "Rent (new flat)",
            },
        ).json()
        assert moved["next_due"] == "2026-06-15" and moved["name"] == "Rent (new flat)"

        assert c.delete(f"/books/{ledger.book_id}/schedules/{s['id']}").status_code == 204
        assert c.get(f"/books/{ledger.book_id}/schedules/{s['id']}").status_code == 404

    def test_an_invalid_recurrence_is_422(self, ledger: Ledger):
        body = _schedule_body(ledger)
        body["recurrence"] = {
            "frequency": "WEEKLY",
            "start": "2026-01-01",
            "monthly_by_weekday": True,
        }
        assert (
            ledger.client.post(
                f"/books/{ledger.book_id}/schedules", json=body
            ).status_code
            == 422
        )

    def test_a_template_account_that_does_not_exist_is_404(self, ledger: Ledger):
        body = _schedule_body(ledger)
        body["template"]["legs"][0]["account_id"] = "01a07000-0000-7000-8000-000000000000"
        assert (
            ledger.client.post(
                f"/books/{ledger.book_id}/schedules", json=body
            ).status_code
            == 404
        )


class TestDuePanelAndPosting:
    def test_due_panel_lists_overdue_occurrences_with_a_quick_amount(
        self, ledger: Ledger
    ):
        c = ledger.client
        # The book's clock sits at 2026-01-31 12:00 (conftest START).
        c.post(f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger))
        panel = c.get(f"/books/{ledger.book_id}/schedules/due").json()
        assert panel["as_of"] == "2026-01-01" or panel["occurrences"] >= 1
        (row,) = panel["due"]
        assert row["schedule"]["name"] == "Rent"
        assert row["amount"]["text"] == "1200.00" and row["amount"]["commodity"] == "EUR"
        assert row["pending"][0] == "2026-01-01"

    def test_estimate_templates_have_no_quick_amount(self, ledger: Ledger):
        body = _schedule_body(ledger, name="Salary")
        body["template"]["legs"] = [
            {"account_id": ledger.checking, "value": "2500.00"},
            {"account_id": ledger.salary},
        ]
        body["template"]["estimate"] = True
        ledger.client.post(f"/books/{ledger.book_id}/schedules", json=body)
        due = ledger.client.get(f"/books/{ledger.book_id}/schedules/due").json()["due"]
        assert due and due[0]["amount"] is None

    def test_post_an_occurrence_and_the_schedule_advances(self, ledger: Ledger):
        c = ledger.client
        sid = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        ).json()["id"]
        posted = c.post(f"/books/{ledger.book_id}/schedules/{sid}/post", json={})
        assert posted.status_code == 201
        txn = posted.json()
        assert txn["date"] == "2026-01-01" and txn["memo"] == "Monthly rent"
        assert txn["imported_id"] == f"sched:{sid}:2026-01-01"
        assert int(posted.headers["X-PyMoney-Revision"]) > 0

        after = c.get(f"/books/{ledger.book_id}/schedules/{sid}").json()
        assert after["next_due"] == "2026-02-01" and after["occurrences_done"] == 1
        assert after["last_occurrence"] == "2026-01-01"
        # The transaction is in the register.
        reg = c.get(
            f"/books/{ledger.book_id}/accounts/{ledger.checking}/register",
            params={"start": "2026-01-01", "end": "2026-01-05"},
        ).json()
        assert any(r["transaction_id"] == txn["id"] for r in reg["items"])

    def test_post_with_overrides_for_one_occurrence_only(self, ledger: Ledger):
        c = ledger.client
        sid = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        ).json()["id"]
        txn = c.post(
            f"/books/{ledger.book_id}/schedules/{sid}/post",
            json={
                "legs": [
                    {"account_id": ledger.checking, "value": "-1275.00"},
                    {"account_id": ledger.fuel},
                ],
                "memo": "January rent (deposit topped up)",
            },
        ).json()
        assert txn["memo"] == "January rent (deposit topped up)"
        assert sorted(sp["value"]["minor"] for sp in txn["splits"]) == [-127500, 127500]
        # The template did not change.
        tmpl = c.get(f"/books/{ledger.book_id}/schedules/{sid}").json()["template"]
        assert tmpl["legs"][0]["value"] in ("-1200", "-1200/1", "-6000/5")

    def test_skip_advances_without_posting(self, ledger: Ledger):
        c = ledger.client
        sid = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        ).json()["id"]
        skipped = c.post(f"/books/{ledger.book_id}/schedules/{sid}/skip", json={})
        assert skipped.status_code == 200 and skipped.json()["next_due"] == "2026-02-01"
        assert skipped.json()["occurrences_done"] == 1

    def test_posting_a_finished_schedule_is_422(self, ledger: Ledger):
        c = ledger.client
        body = _schedule_body(ledger)
        body["recurrence"] = {"frequency": "MONTHLY", "start": "2026-01-01", "count": 1}
        sid = c.post(f"/books/{ledger.book_id}/schedules", json=body).json()["id"]
        c.post(f"/books/{ledger.book_id}/schedules/{sid}/post", json={})
        refused = c.post(f"/books/{ledger.book_id}/schedules/{sid}/post", json={})
        assert refused.status_code == 422 and "no occurrence left" in refused.text

    def test_an_idempotency_key_posts_once(self, ledger: Ledger):
        c = ledger.client
        sid = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        ).json()["id"]
        headers = {"Idempotency-Key": "rent-jan", **c.headers}
        first = c.http.post(
            f"/books/{ledger.book_id}/schedules/{sid}/post", json={}, headers=headers
        )
        second = c.http.post(
            f"/books/{ledger.book_id}/schedules/{sid}/post", json={}, headers=headers
        )
        assert first.json()["id"] == second.json()["id"]
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert (
            c.get(f"/books/{ledger.book_id}/schedules/{sid}").json()["occurrences_done"]
            == 1
        )

    def test_sync_carries_schedules(self, ledger: Ledger):
        c = ledger.client
        sid = c.post(
            f"/books/{ledger.book_id}/schedules", json=_schedule_body(ledger)
        ).json()["id"]
        c.post(f"/books/{ledger.book_id}/schedules/{sid}/post", json={})
        page = c.get(f"/books/{ledger.book_id}/changes?since=0").json()
        by_cmd = {
            b["command"]: (len(b["schedules"]), len(b["transactions"]))
            for b in page["batches"]
        }
        assert by_cmd["CreateSchedule"] == (1, 0)
        assert by_cmd["PostScheduledOccurrence"] == (1, 1)


class TestAuthorisation:
    def test_a_viewer_may_read_but_not_change(self, api: Client):
        api.register("owner@example.invalid")
        book_id = api.make_book()
        cash = api.account("Cash", "CASH")
        food = api.account("Food", "EXPENSE")
        api.put(
            f"/books/{book_id}/grants",
            json={"subject": "guest@example.invalid", "role": "viewer"},
        )
        body = {
            "name": "Rent",
            "recurrence": {"frequency": "MONTHLY", "start": "2026-01-01"},
            "template": {
                "legs": [
                    {"account_id": cash["id"], "value": "-1.00"},
                    {"account_id": food["id"]},
                ]
            },
        }
        sid = api.post(f"/books/{book_id}/schedules", json=body).json()["id"]
        api.logout()
        api.register("guest@example.invalid")
        assert api.get(f"/books/{book_id}/schedules").status_code == 200
        assert api.get(f"/books/{book_id}/schedules/due").status_code == 200
        assert api.post(f"/books/{book_id}/schedules", json=body).status_code == 403
        assert (
            api.post(f"/books/{book_id}/schedules/{sid}/post", json={}).status_code == 403
        )

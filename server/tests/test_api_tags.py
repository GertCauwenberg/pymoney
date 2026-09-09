"""HTTP surface for tags: CRUD, attaching them to a leg, and searching by one."""

from __future__ import annotations

from tests.conftest_api import Ledger


class TestTagCrud:
    def test_create_list_and_patch(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        created = client.post(
            f"/books/{book}/tags",
            json={"name": "Holiday", "colour": "#ff8800", "notes": "Spain"},
        )
        assert created.status_code == 201
        tag = created.json()
        assert tag["name"] == "Holiday" and tag["colour"] == "#ff8800"
        assert tag["closed"] is False

        client.post(f"/books/{book}/tags", json={"name": "Work"})
        names = [t["name"] for t in client.get(f"/books/{book}/tags").json()]
        assert names == ["Holiday", "Work"]

        patched = client.patch(
            f"/books/{book}/tags/{tag['id']}", json={"name": "Holidays", "closed": True}
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Holidays"
        open_only = client.get(
            f"/books/{book}/tags", params={"include_closed": False}
        ).json()
        assert [t["name"] for t in open_only] == ["Work"]
        assert [t["name"] for t in client.get(f"/books/{book}/tags").json()] == [
            "Holidays",
            "Work",
        ]

    def test_get_one_and_delete_an_unused_tag(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        created = client.post(f"/books/{book}/tags", json={"name": "Spurious"}).json()
        fetched = client.get(f"/books/{book}/tags/{created['id']}")
        assert fetched.status_code == 200 and fetched.json()["name"] == "Spurious"
        gone = client.delete(f"/books/{book}/tags/{created['id']}")
        assert gone.status_code == 204
        assert client.get(f"/books/{book}/tags/{created['id']}").status_code == 404

    def test_a_duplicate_name_is_a_conflict(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        client.post(f"/books/{book}/tags", json={"name": "Holiday"})
        clash = client.post(f"/books/{book}/tags", json={"name": "holiday"})
        assert clash.status_code == 409

    def test_search_filters_by_substring(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        for name in ("Holiday 2026", "Work trip", "House"):
            client.post(f"/books/{book}/tags", json={"name": name})
        hit = client.get(f"/books/{book}/tags", params={"search": "ho"}).json()
        assert sorted(t["name"] for t in hit) == ["Holiday 2026", "House"]


class TestTagsOnTransactions:
    def _tag(self, ledger: Ledger, name: str) -> str:
        return ledger.client.post(
            f"/books/{ledger.book_id}/tags", json={"name": name}
        ).json()["id"]

    def test_a_leg_carries_its_tags_through_post_and_patch(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        holiday, work = self._tag(ledger, "Holiday"), self._tag(ledger, "Work")
        posted = client.post_transaction(
            "2026-05-18",
            [
                {"account_id": ledger.checking, "value": "-47.50"},
                {
                    "account_id": ledger.groceries,
                    "tag_ids": [holiday, work, holiday],
                },
            ],
            memo="Holiday shop",
        )
        assert posted.status_code == 201
        legs = {s["account_id"]: s for s in posted.json()["splits"]}
        assert legs[ledger.checking]["tag_ids"] == []
        # Duplicates collapse; order is kept.
        assert legs[ledger.groceries]["tag_ids"] == [holiday, work]

        tx_id = posted.json()["id"]
        patched = client.patch(
            f"/books/{book}/transactions/{tx_id}",
            json={
                "legs": [
                    {"account_id": ledger.checking, "value": "-47.50"},
                    {"account_id": ledger.groceries, "tag_ids": [work]},
                ]
            },
        )
        assert patched.status_code == 200
        legs = {s["account_id"]: s for s in patched.json()["splits"]}
        assert legs[ledger.groceries]["tag_ids"] == [work]

    def test_an_unknown_tag_on_a_leg_is_refused(self, ledger: Ledger):
        missing = "01a00000-0000-7000-8000-000000000000"
        response = ledger.client.post_transaction(
            "2026-05-18",
            [
                {"account_id": ledger.checking, "value": "-1.00"},
                {"account_id": ledger.groceries, "tag_ids": [missing]},
            ],
        )
        assert response.status_code == 404

    def test_a_tag_in_use_cannot_be_deleted(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        holiday = self._tag(ledger, "Holiday")
        client.post_transaction(
            "2026-05-18",
            [
                {"account_id": ledger.checking, "value": "-47.50"},
                {"account_id": ledger.groceries, "tag_ids": [holiday]},
            ],
        )
        refused = client.delete(f"/books/{book}/tags/{holiday}")
        assert refused.status_code == 409

    def test_search_by_tag(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        holiday = self._tag(ledger, "Holiday")
        tagged = client.post_transaction(
            "2026-05-18",
            [
                {"account_id": ledger.checking, "value": "-47.50"},
                {"account_id": ledger.groceries, "tag_ids": [holiday]},
            ],
            memo="tagged one",
        ).json()
        client.post_transaction(
            "2026-05-19",
            [
                {"account_id": ledger.checking, "value": "-9.00"},
                {"account_id": ledger.groceries},
            ],
            memo="plain one",
        )
        hits = client.get(
            f"/books/{book}/transactions/search", params={"tag_id": holiday}
        ).json()
        assert [t["id"] for t in hits["items"]] == [tagged["id"]]

    def test_a_created_tag_travels_on_the_next_sync(self, ledger: Ledger):
        client, book = ledger.client, ledger.book_id
        before = int(client.get(f"/books/{book}/tags").headers["x-pymoney-revision"])
        holiday = self._tag(ledger, "Holiday")
        page = client.get(f"/books/{book}/changes", params={"since": before}).json()
        tag_names = [t["name"] for batch in page["batches"] for t in batch["tags"]]
        assert tag_names == ["Holiday"]
        assert any(t["id"] == holiday for batch in page["batches"] for t in batch["tags"])

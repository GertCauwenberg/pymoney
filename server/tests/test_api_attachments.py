"""HTTP: receipts and invoices pinned to a transaction.

The bytes go to the filesystem, the link to the database; the tests check both
sides, the authentication in front of them, and that nothing is left behind
when the transaction or the book goes.
"""

from __future__ import annotations

from pathlib import Path

from pymoney.api.testing import test_app as make_test_app
from tests.conftest_api import Ledger

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n% a tiny pdf\n%%EOF\n"


def _post_transaction(ledger: Ledger) -> str:
    return ledger.client.post_transaction(
        "2026-02-10",
        [
            {"account_id": ledger.checking, "value": "-20.00"},
            {"account_id": ledger.groceries},
        ],
        memo="Lunch",
    ).json()["id"]


def _attach(ledger: Ledger, tx: str, name: str, data: bytes, ctype: str):
    return ledger.client.post(
        f"/books/{ledger.book_id}/transactions/{tx}/attachments",
        files={"file": (name, data, ctype)},
    )


class TestAttachments:
    def test_upload_list_download_delete(self, ledger: Ledger, tmp_path):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)

        created = _attach(ledger, tx, "receipt.png", PNG, "image/png")
        assert created.status_code == 201, created.text
        att = created.json()
        assert att["filename"] == "receipt.png"
        assert att["content_type"] == "image/png"
        assert att["byte_size"] == len(PNG)
        assert att["transaction_id"] == tx
        assert att["uploaded_by"] == "owner@example.invalid"
        assert len(att["sha256"]) == 64

        # The blob is a real file under the configured directory, not in the DB.
        blob = Path(tmp_path) / "attachments" / ledger.book_id / att["id"]
        assert blob.read_bytes() == PNG

        listed = ledger.client.get(f"{base}/transactions/{tx}/attachments").json()
        assert [a["id"] for a in listed] == [att["id"]]

        got = ledger.client.get(f"{base}/attachments/{att['id']}")
        assert got.status_code == 200
        assert got.content == PNG
        assert got.headers["content-type"].startswith("image/png")
        assert "inline" in got.headers["content-disposition"]
        assert "receipt.png" in got.headers["content-disposition"]

        assert ledger.client.delete(f"{base}/attachments/{att['id']}").status_code == 204
        assert ledger.client.get(f"{base}/transactions/{tx}/attachments").json() == []
        assert not blob.exists()
        assert ledger.client.get(f"{base}/attachments/{att['id']}").status_code == 404

    def test_a_pdf_is_accepted(self, ledger: Ledger):
        tx = _post_transaction(ledger)
        created = _attach(ledger, tx, "invoice.pdf", PDF, "application/pdf")
        assert created.status_code == 201
        assert created.json()["content_type"] == "application/pdf"

    def test_a_disallowed_type_is_refused(self, ledger: Ledger):
        tx = _post_transaction(ledger)
        refused = _attach(ledger, tx, "notes.txt", b"just text", "text/plain")
        assert refused.status_code == 422
        assert "image" in refused.json()["detail"]

    def test_a_lie_about_the_type_is_caught(self, ledger: Ledger):
        tx = _post_transaction(ledger)
        refused = _attach(ledger, tx, "receipt.png", PDF, "image/png")
        assert refused.status_code == 422
        assert "not the image/png" in refused.json()["detail"]

    def test_an_empty_file_is_refused(self, ledger: Ledger):
        tx = _post_transaction(ledger)
        refused = _attach(ledger, tx, "empty.png", b"", "image/png")
        assert refused.status_code == 422

    def test_attaching_to_an_unknown_transaction_is_404(self, ledger: Ledger):
        from uuid import uuid4

        refused = _attach(ledger, str(uuid4()), "r.png", PNG, "image/png")
        assert refused.status_code == 404

    def test_deleting_an_unknown_attachment_is_404(self, ledger: Ledger):
        from uuid import uuid4

        gone = ledger.client.delete(f"/books/{ledger.book_id}/attachments/{uuid4()}")
        assert gone.status_code == 404

    def test_a_filename_is_stripped_to_its_last_component(self, ledger: Ledger):
        tx = _post_transaction(ledger)
        att = _attach(ledger, tx, "../../etc/passwd\\sneaky.png", PNG, "image/png").json()
        assert "/" not in att["filename"] and "\\" not in att["filename"]
        assert att["filename"] == "sneaky.png"

    def test_an_oversize_upload_is_refused(self, tmp_path):
        with make_test_app(tmp_path, max_attachment_mb=1) as client:
            client.register("owner@example.invalid")
            book_id = client.make_book()
            checking = client.account("Checking", "CHECKING")
            groceries = client.account("Groceries", "EXPENSE")
            tx = client.post_transaction(
                "2026-02-10",
                [
                    {"account_id": checking["id"], "value": "-1.00"},
                    {"account_id": groceries["id"]},
                ],
            ).json()["id"]
            big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024 + 1)
            refused = client.post(
                f"/books/{book_id}/transactions/{tx}/attachments",
                files={"file": ("big.png", big, "image/png")},
            )
            assert refused.status_code == 413

    def test_downloading_needs_authentication(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        att = _attach(ledger, tx, "r.png", PNG, "image/png").json()
        ledger.client.http.cookies.clear()
        anon = ledger.client.http.get(f"{base}/attachments/{att['id']}")
        assert anon.status_code == 401

    def test_a_stranger_cannot_even_learn_it_exists(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        att = _attach(ledger, tx, "r.png", PNG, "image/png").json()
        ledger.client.logout()
        ledger.client.register("stranger@example.invalid")
        assert ledger.client.get(f"{base}/attachments/{att['id']}").status_code == 404

    def test_an_editor_may_attach_a_viewer_may_not(self, ledger: Ledger):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        ledger.client.put(
            f"{base}/grants", json={"subject": "seen@example.invalid", "role": "viewer"}
        )
        ledger.client.put(
            f"{base}/grants", json={"subject": "edit@example.invalid", "role": "editor"}
        )

        ledger.client.logout()
        ledger.client.register("seen@example.invalid")
        assert _attach(ledger, tx, "r.png", PNG, "image/png").status_code == 403
        # ...but a viewer can still see and download what an editor attached.

        ledger.client.logout()
        ledger.client.register("edit@example.invalid")
        made = _attach(ledger, tx, "r.png", PNG, "image/png")
        assert made.status_code == 201

        ledger.client.logout()
        ledger.client.login("seen@example.invalid")
        got = ledger.client.get(f"{base}/attachments/{made.json()['id']}")
        assert got.status_code == 200 and got.content == PNG

    def test_deleting_the_transaction_removes_its_attachments(
        self, ledger: Ledger, tmp_path
    ):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        att = _attach(ledger, tx, "r.png", PNG, "image/png").json()
        blob = Path(tmp_path) / "attachments" / ledger.book_id / att["id"]
        assert blob.exists()

        assert ledger.client.delete(f"{base}/transactions/{tx}").status_code == 204
        assert not blob.exists()
        assert ledger.client.get(f"{base}/attachments/{att['id']}").status_code == 404

    def test_deleting_the_book_removes_the_blob_directory(self, ledger: Ledger, tmp_path):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        _attach(ledger, tx, "r.png", PNG, "image/png")
        book_dir = Path(tmp_path) / "attachments" / ledger.book_id
        assert book_dir.is_dir()
        assert ledger.client.delete(base, json={"name": "Test Book"}).status_code == 204
        assert not book_dir.exists()

    def test_a_missing_blob_is_a_clear_410(self, ledger: Ledger, tmp_path):
        base = f"/books/{ledger.book_id}"
        tx = _post_transaction(ledger)
        att = _attach(ledger, tx, "r.png", PNG, "image/png").json()
        (Path(tmp_path) / "attachments" / ledger.book_id / att["id"]).unlink()
        gone = ledger.client.get(f"{base}/attachments/{att['id']}")
        assert gone.status_code == 410

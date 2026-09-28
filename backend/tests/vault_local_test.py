"""Company Vault API tests — RBAC, folders, upload/download round trip, type and
size validation, versions, metadata edits, deletes (GridFS cleanup), stats and
expiry reminders.

Runs against an isolated server and throwaway database (see local_harness.py).
"""
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from bson import ObjectId

from local_harness import api, mongo, test_db, users, harness_env, call  # noqa: F401

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TODAY = datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _upload(api, user, name="contract.pdf", data=PDF, **form):
    return call(api, user, "POST", "/vault/documents", files={"file": (name, data)}, data=form)


def _ok_upload(api, users, **kw):
    r = _upload(api, users["founder"], **kw)
    assert r.status_code == 201, r.text
    return r.json()


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


# ------------------------- RBAC -------------------------

@pytest.mark.parametrize("role", ["admin", "manager", "employee", "intern"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/vault/documents"), ("GET", "/vault/stats"), ("GET", "/vault/folders"),
    ("GET", "/vault/tags"), ("POST", "/vault/reminders/run"),
])
def test_non_founders_forbidden(api, users, role, method, path):
    assert call(api, users[role], method, path).status_code == 403


def test_non_founder_cannot_upload_or_download(api, users, test_db):
    assert _upload(api, users["admin"]).status_code == 403
    assert _upload(api, users["employee"]).status_code == 403
    doc = _ok_upload(api, users, title="RBAC doc")
    for role in ("admin", "employee"):
        assert call(api, users[role], "GET", f"/vault/documents/{doc['id']}/download").status_code == 403
        assert call(api, users[role], "DELETE", f"/vault/documents/{doc['id']}").status_code == 403
        assert call(api, users[role], "PATCH", f"/vault/documents/{doc['id']}", json={"title": "x"}).status_code == 403


def test_unauthenticated(api):
    assert call(api, None, "GET", "/vault/documents").status_code == 401


# ------------------------- folders -------------------------

def test_folder_crud(api, users, test_db):
    f = users["founder"]
    r = call(api, f, "POST", "/vault/folders", json={"name": "  Legal  "})
    assert r.status_code == 201, r.text
    folder = r.json()
    assert folder["name"] == "Legal"
    assert call(api, f, "POST", "/vault/folders", json={"name": "legal"}).status_code == 409
    r = call(api, f, "PATCH", f"/vault/folders/{folder['id']}", json={"name": "Legal & Compliance"})
    assert r.status_code == 200 and r.json()["name"] == "Legal & Compliance"

    doc = _ok_upload(api, users, title="In folder", folder_id=folder["id"])
    listed = call(api, f, "GET", "/vault/folders").json()
    assert next(x for x in listed["folders"] if x["id"] == folder["id"])["count"] == 1
    assert call(api, f, "DELETE", f"/vault/folders/{folder['id']}").status_code == 409

    call(api, f, "PATCH", f"/vault/documents/{doc['id']}", json={"folder_id": None})
    assert call(api, f, "DELETE", f"/vault/folders/{folder['id']}").status_code == 200
    assert test_db.vault_folders.count_documents({"_id": ObjectId(folder["id"])}) == 0
    assert call(api, f, "DELETE", f"/vault/folders/{folder['id']}").status_code == 404
    assert call(api, users["admin"], "POST", "/vault/folders", json={"name": "Nope"}).status_code == 403
    assert test_db.activity_logs.count_documents({"module": "Company Vault", "action": "Created vault folder"}) >= 1


def test_upload_rejects_unknown_folder(api, users):
    r = _upload(api, users["founder"], folder_id=str(ObjectId()))
    assert r.status_code == 422
    assert _upload(api, users["founder"], folder_id="not-an-id").status_code == 400


# ------------------------- upload / download -------------------------

def test_upload_download_round_trip(api, users, test_db):
    data = PDF + b"x" * 300_000
    doc = _ok_upload(api, users, name="Lease Agreement.pdf", data=data, tags="Lease, legal ,lease",
                     description="Office lease", expires_on=_day(200))
    assert doc["title"] == "Lease Agreement"
    assert doc["tags"] == ["lease", "legal"]
    assert doc["size"] == len(data)
    assert doc["checksum"] == hashlib.sha256(data).hexdigest()
    assert doc["content_type"] == "application/pdf"
    assert doc["version"] == 1 and len(doc["versions"]) == 1
    assert doc["uploaded_by"] == users["founder"]["id"]
    assert doc["expiry_status"] == "valid"
    assert "file_id" not in doc and "file_id" not in doc["versions"][0]

    r = call(api, users["founder"], "GET", f"/vault/documents/{doc['id']}/download")
    assert r.status_code == 200
    assert r.content == data
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "Lease Agreement.pdf" in r.headers["content-disposition"]

    r = call(api, users["founder"], "GET", f"/vault/documents/{doc['id']}/download", params={"inline": "true"})
    assert r.headers["content-disposition"].startswith("inline;")
    assert r.headers["content-type"] == "application/pdf"
    assert test_db.activity_logs.count_documents({"action": "Uploaded document", "target": "Lease Agreement"}) == 1


def test_non_previewable_forced_attachment(api, users):
    doc = _ok_upload(api, users, name="notes.txt", data=b"<html><script>alert(1)</script>")
    r = call(api, users["founder"], "GET", f"/vault/documents/{doc['id']}/download", params={"inline": "true"})
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-type"] == "application/octet-stream"


def test_magic_byte_rejection(api, users):
    f = users["founder"]
    assert _upload(api, f, name="fake.pdf", data=b"MZ\x90\x00 not a pdf").status_code == 400
    assert _upload(api, f, name="fake.png", data=PDF).status_code == 400
    assert _upload(api, f, name="fake.jpg", data=PNG).status_code == 400
    assert _upload(api, f, name="fake.docx", data=b"plain text").status_code == 400
    assert _upload(api, f, name="evil.exe", data=b"MZ\x90\x00").status_code == 415
    assert _upload(api, f, name="noext", data=b"abc").status_code == 415
    assert _upload(api, f, name="empty.txt", data=b"").status_code == 400
    assert _upload(api, f, name="img.png", data=PNG).status_code == 201
    assert _upload(api, f, name="img.JPG", data=b"\xff\xd8\xff\xe0" + b"\x00" * 32).status_code == 201


def test_size_limit(api, users, test_db):
    before = test_db["vault.files"].count_documents({})
    r = _upload(api, users["founder"], name="big.pdf", data=PDF + b"0" * (25 * 1024 * 1024))
    assert r.status_code == 413
    assert test_db["vault.files"].count_documents({}) == before


def test_bad_expiry(api, users):
    assert _upload(api, users["founder"], expires_on="31/12/2030").status_code == 422


# ------------------------- versions -------------------------

def test_versions_restore_and_download(api, users, test_db):
    f = users["founder"]
    doc = _ok_upload(api, users, name="policy.pdf", data=PDF + b"v1")
    v2 = PNG + b"v2"
    r = call(api, f, "POST", f"/vault/documents/{doc['id']}/versions",
             files={"file": ("policy-scan.png", v2)}, data={"note": "Signed scan"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 2 and body["file_name"] == "policy-scan.png"
    assert body["content_type"] == "image/png"
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert body["versions"][0]["note"] == "Signed scan"

    assert call(api, f, "GET", f"/vault/documents/{doc['id']}/download").content == v2
    old = call(api, f, "GET", f"/vault/documents/{doc['id']}/versions/1/download")
    assert old.status_code == 200 and old.content == PDF + b"v1"
    assert call(api, f, "GET", f"/vault/documents/{doc['id']}/versions/9/download").status_code == 404

    r = call(api, f, "POST", f"/vault/documents/{doc['id']}/versions/1/restore")
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3 and r.json()["file_name"] == "policy.pdf"
    assert call(api, f, "GET", f"/vault/documents/{doc['id']}/download").content == PDF + b"v1"
    assert call(api, f, "POST", f"/vault/documents/{doc['id']}/versions/3/restore").status_code == 409

    bad = call(api, f, "POST", f"/vault/documents/{doc['id']}/versions", files={"file": ("x.pdf", b"nope")})
    assert bad.status_code == 400
    assert call(api, users["admin"], "POST", f"/vault/documents/{doc['id']}/versions",
                files={"file": ("x.pdf", PDF)}).status_code == 403


# ------------------------- metadata / delete -------------------------

def test_metadata_edit(api, users, test_db):
    f = users["founder"]
    doc = _ok_upload(api, users, name="gst.pdf")
    test_db.vault_documents.update_one({"_id": ObjectId(doc["id"])}, {"$set": {"reminders_sent": ["30d"]}})
    r = call(api, f, "PATCH", f"/vault/documents/{doc['id']}", json={
        "title": "GST Certificate", "tags": ["Tax", "gst"], "description": "Registration",
        "expires_on": _day(5)})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["title"] == "GST Certificate" and out["tags"] == ["tax", "gst"]
    assert out["expiry_status"] == "expiring" and out["days_to_expiry"] == 5
    assert test_db.vault_documents.find_one({"_id": ObjectId(doc["id"])})["reminders_sent"] == []
    assert call(api, f, "PATCH", f"/vault/documents/{doc['id']}", json={"title": "  "}).status_code == 422
    assert call(api, f, "PATCH", f"/vault/documents/{doc['id']}", json={"expires_on": "soon"}).status_code == 422
    r = call(api, f, "PATCH", f"/vault/documents/{doc['id']}", json={"expires_on": None})
    assert r.json()["expires_on"] is None
    assert test_db.activity_logs.count_documents({"action": "Edited document details", "target": "GST Certificate"}) >= 1


def test_delete_removes_gridfs_files(api, users, test_db):
    f = users["founder"]
    doc = _ok_upload(api, users, name="old.pdf")
    call(api, f, "POST", f"/vault/documents/{doc['id']}/versions", files={"file": ("old2.pdf", PDF + b"2")})
    call(api, f, "POST", f"/vault/documents/{doc['id']}/versions/1/restore")
    stored = test_db.vault_documents.find_one({"_id": ObjectId(doc["id"])})
    file_ids = {ObjectId(v["file_id"]) for v in stored["versions"]}
    assert len(file_ids) == 2
    assert test_db["vault.files"].count_documents({"_id": {"$in": list(file_ids)}}) == 2

    assert call(api, f, "DELETE", f"/vault/documents/{doc['id']}").status_code == 200
    assert test_db["vault.files"].count_documents({"_id": {"$in": list(file_ids)}}) == 0
    assert test_db["vault.chunks"].count_documents({"files_id": {"$in": list(file_ids)}}) == 0
    assert call(api, f, "GET", f"/vault/documents/{doc['id']}").status_code == 404
    assert call(api, f, "DELETE", f"/vault/documents/{doc['id']}").status_code == 404
    assert call(api, f, "GET", "/vault/documents/not-an-id").status_code == 400


# ------------------------- list / stats -------------------------

def test_list_filters_and_pagination(api, users):
    f = users["founder"]
    folder = call(api, f, "POST", "/vault/folders", json={"name": "Insurance"}).json()
    a = _ok_upload(api, users, name="policy-a.pdf", title="Fleet insurance (a+b)", folder_id=folder["id"],
                   tags="insurance", expires_on=_day(10))
    b = _ok_upload(api, users, name="policy-b.pdf", title="Office insurance", folder_id=folder["id"],
                   tags="insurance,office", expires_on=_day(-3))

    def ids(**params):
        r = call(api, f, "GET", "/vault/documents", params=params)
        assert r.status_code == 200, r.text
        return [d["id"] for d in r.json()["items"]]

    assert set(ids(folder_id=folder["id"])) == {a["id"], b["id"]}
    assert ids(folder_id=folder["id"], tag="office") == [b["id"]]
    assert ids(q="(a+b)") == [a["id"]]  # regex metacharacters are escaped
    assert a["id"] in ids(expiry="expiring") and b["id"] not in ids(expiry="expiring")
    assert b["id"] in ids(expiry="expired") and a["id"] not in ids(expiry="expired")
    assert a["id"] not in ids(folder_id="unfiled")

    page1 = call(api, f, "GET", "/vault/documents", params={"page_size": 1, "folder_id": folder["id"]}).json()
    page2 = call(api, f, "GET", "/vault/documents", params={"page_size": 1, "page": 2, "folder_id": folder["id"]}).json()
    assert page1["total"] == 2 and page1["pages"] == 2
    assert {page1["items"][0]["id"], page2["items"][0]["id"]} == {a["id"], b["id"]}

    tags = {t["tag"]: t["count"] for t in call(api, f, "GET", "/vault/tags").json()}
    assert tags["insurance"] >= 2


def test_expiry_sort_keeps_undated_documents_last(api, users):
    f = users["founder"]
    folder = call(api, f, "POST", "/vault/folders", json={"name": "Sort check"}).json()
    undated = _ok_upload(api, users, name="memo.pdf", folder_id=folder["id"])
    late = _ok_upload(api, users, name="late.pdf", folder_id=folder["id"], expires_on=_day(90))
    soon = _ok_upload(api, users, name="soon.pdf", folder_id=folder["id"], expires_on=_day(5))

    def page(n, size):
        return call(api, f, "GET", "/vault/documents",
                    params={"folder_id": folder["id"], "sort": "expiry", "page": n, "page_size": size}).json()

    full = page(1, 10)
    assert full["total"] == 3 and [d["id"] for d in full["items"]] == [soon["id"], late["id"], undated["id"]]
    assert [page(n, 2)["items"][i]["id"] for n, i in ((1, 0), (1, 1), (2, 0))] == [soon["id"], late["id"], undated["id"]]
    assert page(2, 2)["total"] == 3 and len(page(2, 2)["items"]) == 1


def test_stats(api, users, test_db):
    f = users["founder"]
    stats = call(api, f, "GET", "/vault/stats").json()
    docs = list(test_db.vault_documents.find({}))
    assert stats["count"] == len(docs)
    assert stats["total_size"] == sum(d["size"] for d in docs)
    today = TODAY.isoformat()
    horizon = _day(30)
    assert stats["expired"] == sum(1 for d in docs if d.get("expires_on") and d["expires_on"] < today)
    assert stats["expiring_30d"] == sum(1 for d in docs if d.get("expires_on") and today <= d["expires_on"] <= horizon)
    assert sum(g["count"] for g in stats["by_folder"]) == len(docs)
    assert stats["storage_size"] >= stats["total_size"] or stats["stored_files"] >= len(docs)


# ------------------------- expiry reminders -------------------------

def test_expiry_reminders_sent_once(api, users, test_db):
    f = users["founder"]
    test_db.vault_documents.update_many({}, {"$set": {"reminders_sent": ["30d", "7d", "expired"]}})
    d30 = _ok_upload(api, users, name="licence.pdf", title="Trade licence", expires_on=_day(25))
    d7 = _ok_upload(api, users, name="fitness.pdf", title="Fitness cert", expires_on=_day(6))
    dx = _ok_upload(api, users, name="permit.pdf", title="Road permit", expires_on=_day(-1))
    far = _ok_upload(api, users, name="deed.pdf", title="Deed", expires_on=_day(90))

    r = call(api, f, "POST", "/vault/reminders/run")
    assert r.status_code == 200 and r.json()["sent"] == 3

    def notes(doc):
        return list(test_db.notifications.find({"user_id": f["id"], "link": f"/company-vault?doc={doc['id']}"}))

    assert len(notes(d30)) == 1 and "expires on" in notes(d30)[0]["body"]
    assert len(notes(d7)) == 1
    assert len(notes(dx)) == 1 and notes(dx)[0]["title"] == "Document expired"
    assert notes(far) == []
    assert set(test_db.vault_documents.find_one({"_id": ObjectId(d7["id"])})["reminders_sent"]) == {"30d", "7d"}

    # Idempotent: nothing new on a second run.
    assert call(api, f, "POST", "/vault/reminders/run").json()["sent"] == 0

    # Crossing into the 7-day window sends the next stage once.
    test_db.vault_documents.update_one({"_id": ObjectId(d30["id"])}, {"$set": {"expires_on": _day(7)}})
    assert call(api, f, "POST", "/vault/reminders/run").json()["sent"] == 1
    assert len(notes(d30)) == 2

    # Changing the expiry through the API resets the reminders.
    call(api, f, "PATCH", f"/vault/documents/{d7['id']}", json={"expires_on": _day(20)})
    assert call(api, f, "POST", "/vault/reminders/run").json()["sent"] == 1
    assert len(notes(d7)) == 2

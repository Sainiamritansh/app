"""Company Vault (mounted at /api/vault): Founder-only document storage.

Metadata lives in `vault_documents`, folders in `vault_folders`; file bytes live in
GridFS (bucket "vault"). Storage is isolated behind `_put_bytes` / `_get_stream` /
`_delete` so an object store (e.g. S3) can replace GridFS without touching routes.

Every version of a document keeps its own stored file. Restoring an old version
appends a new version that points at the same stored file (no byte copy), so
deletes de-duplicate file ids before removing them.

Expiry: `expires_on` is a calendar date (YYYY-MM-DD) compared against "today" in
VAULT_TZ (default Asia/Kolkata). `send_expiry_reminders(db)` notifies every
Founder once at 30 days, once at 7 days and once when expired; the stages already
sent are tracked in `reminders_sent` and reset whenever the expiry date changes.

Server wiring (outside this module): call `await ensure_indexes(db)` on startup and
`await send_expiry_reminders(db)` periodically from the background loop.
"""
# No `from __future__ import annotations`: FastAPI resolves Form/File/Query
# parameters and the Pydantic bodies below from runtime annotations.
import hashlib
import logging
import math
import os
import re
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
# pyrefly: ignore [missing-import]
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from gridfs.errors import NoFile

from db import get_db
from auth_utils import get_current_user
from models import UserPublic
from hub_utils import log_activity, notify, oid, utc_iso
from permissions import can

router = APIRouter(prefix="/vault", tags=["company-vault"])
logger = logging.getLogger("wavygo.vault")

MODULE = "Company Vault"
LINK = "/company-vault"
BUCKET = "vault"
MAX_BYTES = 25 * 1024 * 1024
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100
EXPIRY_WINDOW_DAYS = 30
MAX_TAGS = 20
MAX_TAG_LEN = 40
VAULT_TZ = os.environ.get("VAULT_TZ", "Asia/Kolkata")

# Extension -> canonical content type. The client-supplied type is never trusted.
ALLOWED_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
}
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
SORTS = {
    "updated": [("updated_at", -1), ("_id", -1)],
    "title": [("title_lower", 1), ("_id", 1)],
    "size": [("size", -1), ("_id", -1)],
    "expiry": [("expires_on", 1), ("_id", 1)],
}
REMINDER_STAGES = ("30d", "7d", "expired")
HIDDEN_FIELDS = ("title_lower", "reminders_sent", "file_id")


# ------------------------- storage (swap for S3 here) -------------------------

def _bucket(db) -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(db, bucket_name=BUCKET)


async def _put_bytes(db, data: bytes, filename: str, content_type: str) -> str:
    """Store bytes, return an opaque storage id."""
    file_id = await _bucket(db).upload_from_stream(
        filename, data, metadata={"content_type": content_type})
    return str(file_id)


async def _get_stream(db, storage_id: str) -> tuple[AsyncIterator[bytes], int]:
    """Open a stored file: (async chunk iterator, length). Raises 404 if missing."""
    try:
        stream = await _bucket(db).open_download_stream(ObjectId(storage_id))
    except (NoFile, Exception) as e:  # bad id or missing file
        logger.warning("Vault file %s unavailable: %s", storage_id, e)
        raise HTTPException(404, "Stored file not found")

    async def chunks():
        try:
            while True:
                chunk = await stream.readchunk()
                if not chunk:
                    break
                yield chunk
        finally:
            stream.close()

    return chunks(), stream.length


async def _delete(db, storage_id: str) -> None:
    try:
        await _bucket(db).delete(ObjectId(storage_id))
    except NoFile:
        pass


# ------------------------- helpers -------------------------

def _require(current: UserPublic, action: str) -> None:
    if not can(current.role, action):
        raise HTTPException(403, "Only Founders can access the Company Vault")


def _today() -> date:
    return datetime.now(ZoneInfo(VAULT_TZ)).date()


def _parse_expiry(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(422, "expires_on must be a date (YYYY-MM-DD)")


def _clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    for t in tags or []:
        t = re.sub(r"\s+", " ", str(t)).strip().lower()
        if not t:
            continue
        if len(t) > MAX_TAG_LEN:
            raise HTTPException(422, f"Tags can be at most {MAX_TAG_LEN} characters")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise HTTPException(422, f"At most {MAX_TAGS} tags per document")
    return out


def _clean_text(value: Optional[str], field: str, max_len: int, required: bool = False) -> Optional[str]:
    value = (value or "").strip()
    if required and not value:
        raise HTTPException(422, f"{field} is required")
    if len(value) > max_len:
        raise HTTPException(422, f"{field} can be at most {max_len} characters")
    return value or None


def _content_disposition(kind: str, name: str) -> str:
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", name) or "file"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _safe_filename(name: Optional[str]) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    return name[:200] or "file"


def _detect_type(filename: str, data: bytes) -> str:
    """Return the canonical content type, or raise when the type is not allowed
    or the bytes don't look like the claimed type."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ctype = ALLOWED_TYPES.get(ext)
    if not ctype:
        raise HTTPException(415, "Unsupported file type. Allowed: PDF, PNG, JPG, WEBP, DOCX, XLSX, PPTX, CSV, TXT")
    head = data[:16]
    ok = True
    if ext == "pdf":
        ok = data[:1024].lstrip(b"\xef\xbb\xbf\r\n\t ").startswith(b"%PDF-")
    elif ext == "png":
        ok = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif ext in ("jpg", "jpeg"):
        ok = head.startswith(b"\xff\xd8\xff")
    elif ext == "webp":
        ok = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    elif ext in ("docx", "xlsx", "pptx"):
        ok = head.startswith(b"PK\x03\x04")
    elif ext in ("csv", "txt"):
        ok = b"\x00" not in data[:8192]
    if not ok:
        raise HTTPException(400, f"File content does not match the .{ext} extension")
    return ctype


async def _read_upload(file: UploadFile) -> tuple[bytes, str, str]:
    """Read and validate an upload: (bytes, safe filename, content type)."""
    data = await file.read(MAX_BYTES + 1)
    await file.close()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 25 MB")
    if not data:
        raise HTTPException(400, "File is empty")
    name = _safe_filename(file.filename)
    return data, name, _detect_type(name, data)


def _expiry_state(expires_on: Optional[str], today: date) -> tuple[Optional[str], Optional[int]]:
    if not expires_on:
        return None, None
    days = (date.fromisoformat(expires_on) - today).days
    if days < 0:
        return "expired", days
    if days <= EXPIRY_WINDOW_DAYS:
        return "expiring", days
    return "valid", days


def _out(doc: dict, today: Optional[date] = None) -> dict:
    today = today or _today()
    out = {k: v for k, v in doc.items() if k not in HIDDEN_FIELDS and k != "_id"}
    out["id"] = str(doc["_id"])
    out["versions"] = [
        {k: v for k, v in ver.items() if k != "file_id"}
        for ver in sorted(doc.get("versions", []), key=lambda v: v["version"], reverse=True)
    ]
    out["expiry_status"], out["days_to_expiry"] = _expiry_state(doc.get("expires_on"), today)
    return out


async def _load(db, doc_id: str) -> dict:
    doc = await db.vault_documents.find_one({"_id": oid(doc_id)})
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


async def _folder_name(db, folder_id: Optional[str]) -> Optional[str]:
    """Validate a folder id; returns the folder name (None for unfiled)."""
    if not folder_id:
        return None
    folder = await db.vault_folders.find_one({"_id": oid(folder_id)})
    if not folder:
        raise HTTPException(422, "Folder not found")
    return folder["name"]


async def _notify_founders(db, title: str, body: str, link: str, kind: str = "info",
                           exclude: Optional[str] = None) -> int:
    sent = 0
    async for u in db.users.find({"role": "Founder", "status": {"$ne": "deactivated"}}, {"_id": 1}):
        uid = str(u["_id"])
        if uid == exclude:
            continue
        await notify(db, uid, title, body, kind=kind, link=link)
        sent += 1
    return sent


def _doc_link(doc_id) -> str:
    return f"{LINK}?doc={doc_id}"


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _stream_response(chunks, length: int, content_type: str, filename: str, inline: bool) -> StreamingResponse:
    inline = inline and content_type in INLINE_TYPES
    return StreamingResponse(
        chunks,
        media_type=content_type if content_type in INLINE_TYPES else "application/octet-stream",
        headers={
            "Content-Length": str(length),
            "Content-Disposition": _content_disposition("inline" if inline else "attachment", filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------- setup / background -------------------------

async def ensure_indexes(db) -> None:
    await db.vault_folders.create_index("name_lower", unique=True)
    await db.vault_documents.create_index([("updated_at", -1), ("_id", -1)])
    await db.vault_documents.create_index("folder_id")
    await db.vault_documents.create_index("tags")
    await db.vault_documents.create_index("expires_on", sparse=True)
    await db.vault_documents.create_index("title_lower")


def _stage_for(days: int) -> Optional[str]:
    if days < 0:
        return "expired"
    if days <= 7:
        return "7d"
    if days <= 30:
        return "30d"
    return None


async def send_expiry_reminders(db) -> int:
    """Notify Founders about documents 30 days / 7 days from expiry and expired ones.
    Each stage is sent at most once per expiry date. Returns notifications sent."""
    today = _today()
    horizon = (today + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    sent = 0
    cursor = db.vault_documents.find(
        {"expires_on": {"$ne": None, "$lte": horizon}, "reminders_sent": {"$ne": "expired"}},
        {"title": 1, "expires_on": 1, "reminders_sent": 1},
    )
    async for doc in cursor:
        days = (date.fromisoformat(doc["expires_on"]) - today).days
        stage = _stage_for(days)
        if not stage or stage in (doc.get("reminders_sent") or []):
            continue
        # Mark this stage and every earlier one, atomically, so a late upload that is
        # already inside the 7-day window never gets a stale 30-day reminder afterwards.
        stages = list(REMINDER_STAGES[: REMINDER_STAGES.index(stage) + 1])
        res = await db.vault_documents.update_one(
            {"_id": doc["_id"], "expires_on": doc["expires_on"], "reminders_sent": {"$ne": stage}},
            {"$addToSet": {"reminders_sent": {"$each": stages}}},
        )
        if res.modified_count != 1:
            continue
        exp = date.fromisoformat(doc["expires_on"]).strftime("%d %b %Y")
        if stage == "expired":
            title, body, kind = "Document expired", f"“{doc['title']}” expired on {exp}.", "warning"
        else:
            title = "Document expiring soon"
            body = f"“{doc['title']}” expires on {exp} ({days} day{'s' if days != 1 else ''} left)."
            kind = "warning"
        sent += await _notify_founders(db, title, body, _doc_link(doc["_id"]), kind=kind)
    return sent


# ------------------------- folders -------------------------

class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _folder_name_clean(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        raise HTTPException(422, "Folder name is required")
    return name


@router.get("/folders")
async def list_folders(current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    db = get_db()
    counts = {r["_id"]: r for r in await db.vault_documents.aggregate([
        {"$group": {"_id": "$folder_id", "count": {"$sum": 1}, "size": {"$sum": "$size"}}},
    ]).to_list(None)}
    folders = []
    async for f in db.vault_folders.find({}).sort("name_lower", 1):
        c = counts.get(str(f["_id"]), {})
        folders.append({"id": str(f["_id"]), "name": f["name"], "count": c.get("count", 0),
                        "size": c.get("size", 0), "created_at": f.get("created_at")})
    unfiled = counts.get(None, {})
    return {"folders": folders, "unfiled_count": unfiled.get("count", 0),
            "total_count": sum(c["count"] for c in counts.values())}


@router.post("/folders", status_code=201)
async def create_folder(body: FolderIn, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    name = _folder_name_clean(body.name)
    # Explicit check; the unique index from ensure_indexes() is the race-proof backstop.
    if await db.vault_folders.find_one({"name_lower": name.lower()}, {"_id": 1}):
        raise HTTPException(409, "A folder with this name already exists")
    doc = {"name": name, "name_lower": name.lower(), "created_by": current.id, "created_at": utc_iso()}
    try:
        res = await db.vault_folders.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "A folder with this name already exists")
    await log_activity(db, current, "Created vault folder", MODULE, target=name)
    return {"id": str(res.inserted_id), "name": name, "count": 0, "size": 0, "created_at": doc["created_at"]}


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderIn, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    name = _folder_name_clean(body.name)
    if await db.vault_folders.find_one({"name_lower": name.lower(), "_id": {"$ne": oid(folder_id)}}, {"_id": 1}):
        raise HTTPException(409, "A folder with this name already exists")
    try:
        old = await db.vault_folders.find_one_and_update(
            {"_id": oid(folder_id)}, {"$set": {"name": name, "name_lower": name.lower()}})
    except DuplicateKeyError:
        raise HTTPException(409, "A folder with this name already exists")
    if not old:
        raise HTTPException(404, "Folder not found")
    await log_activity(db, current, "Renamed vault folder", MODULE, target=f"{old['name']} → {name}")
    return {"id": folder_id, "name": name}


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    folder = await db.vault_folders.find_one({"_id": oid(folder_id)})
    if not folder:
        raise HTTPException(404, "Folder not found")
    if await db.vault_documents.count_documents({"folder_id": folder_id}, limit=1):
        raise HTTPException(409, "Folder is not empty. Move or delete its documents first.")
    await db.vault_folders.delete_one({"_id": folder["_id"]})
    await log_activity(db, current, "Deleted vault folder", MODULE, target=folder["name"])
    return {"ok": True}


# ------------------------- documents -------------------------

@router.get("/tags")
async def list_tags(current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    rows = await get_db().vault_documents.aggregate([
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": 200},
    ]).to_list(None)
    return [{"tag": r["_id"], "count": r["count"]} for r in rows]


@router.get("/documents")
async def list_documents(
    folder_id: Optional[str] = Query(None, description="Folder id, or 'unfiled'"),
    tag: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=200),
    expiry: Optional[str] = Query(None, pattern="^(expiring|expired|any)$"),
    sort: str = Query("updated", pattern="^(updated|title|size|expiry)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    current: UserPublic = Depends(get_current_user),
):
    _require(current, "vault.view")
    db = get_db()
    today = _today()
    flt: dict = {}
    if folder_id == "unfiled":
        flt["folder_id"] = None
    elif folder_id:
        oid(folder_id)
        flt["folder_id"] = folder_id
    if tag and tag.strip():
        flt["tags"] = tag.strip().lower()
    if q and q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        flt["$or"] = [{"title": rx}, {"file_name": rx}, {"description": rx}, {"tags": rx}]
    if expiry == "expired":
        flt["expires_on"] = {"$ne": None, "$lt": today.isoformat()}
    elif expiry == "expiring":
        flt["expires_on"] = {"$gte": today.isoformat(),
                             "$lte": (today + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()}
    elif expiry == "any":
        flt["expires_on"] = {"$ne": None}
    total = await db.vault_documents.count_documents(flt)
    skip = (page - 1) * page_size
    if sort == "expiry" and "expires_on" not in flt:
        # Soonest expiry first, then documents without an expiry date (Mongo would sort nulls first).
        dated = {**flt, "expires_on": {"$ne": None}}
        n_dated = await db.vault_documents.count_documents(dated)
        docs = await db.vault_documents.find(dated).sort(SORTS["expiry"]) \
            .skip(skip).limit(page_size).to_list(page_size)
        if len(docs) < page_size:
            docs += await db.vault_documents.find({**flt, "expires_on": None}).sort(SORTS["updated"]) \
                .skip(max(0, skip - n_dated)).limit(page_size - len(docs)).to_list(page_size)
    else:
        docs = await db.vault_documents.find(flt).sort(SORTS[sort]) \
            .skip(skip).limit(page_size).to_list(page_size)
    return {
        "items": [_out(d, today) for d in docs],
        "total": total, "page": page, "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


@router.post("/documents", status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    folder_id: Optional[str] = Form(None),
    tags: Optional[str] = Form(None, description="Comma-separated"),
    description: Optional[str] = Form(None),
    expires_on: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    current: UserPublic = Depends(get_current_user),
):
    _require(current, "vault.manage")
    db = get_db()
    folder_id = (folder_id or "").strip() or None
    folder_name = await _folder_name(db, folder_id)
    tag_list = _clean_tags(tags)
    expiry = _parse_expiry(expires_on)
    description = _clean_text(description, "Description", 2000)
    note = _clean_text(note, "Note", 300)
    data, name, ctype = await _read_upload(file)
    title = _clean_text(title, "Title", 200) or (name.rsplit(".", 1)[0] if "." in name else name)

    now = utc_iso()
    checksum = hashlib.sha256(data).hexdigest()
    storage_id = await _put_bytes(db, data, name, ctype)
    version = {
        "version": 1, "file_id": storage_id, "file_name": name, "content_type": ctype,
        "size": len(data), "checksum": checksum, "uploaded_by": current.id,
        "uploaded_by_name": current.name, "uploaded_at": now, "note": note,
    }
    doc = {
        "title": title, "title_lower": title.lower(), "folder_id": folder_id, "tags": tag_list,
        "description": description, "file_name": name, "content_type": ctype, "size": len(data),
        "checksum": checksum, "file_id": storage_id, "version": 1,
        "uploaded_by": current.id, "uploaded_by_name": current.name,
        "created_at": now, "updated_at": now, "expires_on": expiry, "reminders_sent": [],
        "versions": [version],
    }
    try:
        res = await db.vault_documents.insert_one(doc)
    except Exception:
        await _delete(db, storage_id)
        raise
    doc["_id"] = res.inserted_id
    await log_activity(db, current, "Uploaded document", MODULE, target=title,
                       meta={"document_id": str(res.inserted_id), "folder": folder_name, "size": len(data)})
    await _notify_founders(db, "Document added to Vault",
                           f"{current.name} uploaded “{title}” ({_human_size(len(data))}).",
                           _doc_link(res.inserted_id), exclude=current.id)
    return _out(doc)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    return _out(await _load(get_db(), doc_id))


class DocumentPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    folder_id: Optional[str] = None
    tags: Optional[list[str]] = None
    description: Optional[str] = Field(None, max_length=2000)
    expires_on: Optional[str] = None


@router.patch("/documents/{doc_id}")
async def update_document(doc_id: str, body: DocumentPatch, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    doc = await _load(db, doc_id)
    fields = body.model_dump(exclude_unset=True)
    updates: dict = {}
    if "title" in fields:
        title = _clean_text(fields["title"], "Title", 200, required=True)
        updates.update(title=title, title_lower=title.lower())
    if "folder_id" in fields:
        folder_id = (fields["folder_id"] or "").strip() or None
        await _folder_name(db, folder_id)
        updates["folder_id"] = folder_id
    if "tags" in fields:
        updates["tags"] = _clean_tags(fields["tags"] or [])
    if "description" in fields:
        updates["description"] = _clean_text(fields["description"], "Description", 2000)
    if "expires_on" in fields:
        updates["expires_on"] = _parse_expiry(fields["expires_on"])
        if updates["expires_on"] != doc.get("expires_on"):
            updates["reminders_sent"] = []
    if not updates:
        return _out(doc)
    updates["updated_at"] = utc_iso()
    doc = await db.vault_documents.find_one_and_update(
        {"_id": doc["_id"]}, {"$set": updates}, return_document=ReturnDocument.AFTER)
    if not doc:
        raise HTTPException(404, "Document not found")
    changed = [k for k in fields if k in updates]
    await log_activity(db, current, "Edited document details", MODULE, target=doc["title"],
                       meta={"document_id": doc_id, "fields": changed})
    return _out(doc)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    doc = await db.vault_documents.find_one_and_delete({"_id": oid(doc_id)})
    if not doc:
        raise HTTPException(404, "Document not found")
    for storage_id in {v["file_id"] for v in doc.get("versions", [])} | {doc.get("file_id")}:
        if storage_id:
            await _delete(db, storage_id)
    await log_activity(db, current, "Deleted document", MODULE, target=doc["title"],
                       meta={"document_id": doc_id, "versions": len(doc.get("versions", []))})
    await _notify_founders(db, "Document removed from Vault",
                           f"{current.name} deleted “{doc['title']}”.", LINK, exclude=current.id)
    return {"ok": True}


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, inline: bool = False, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    db = get_db()
    doc = await _load(db, doc_id)
    chunks, length = await _get_stream(db, doc["file_id"])
    if not inline:
        await log_activity(db, current, "Downloaded document", MODULE, target=doc["title"],
                           meta={"document_id": doc_id, "version": doc["version"]})
    return _stream_response(chunks, length, doc["content_type"], doc["file_name"], inline)


def _find_version(doc: dict, number: int) -> dict:
    for v in doc.get("versions", []):
        if v["version"] == number:
            return v
    raise HTTPException(404, "Version not found")


async def _append_version(db, doc: dict, current: UserPublic, version: dict) -> dict:
    """Append a version with an optimistic check on the current version number."""
    number = doc["version"] + 1
    version = {**version, "version": number, "uploaded_by": current.id,
               "uploaded_by_name": current.name, "uploaded_at": utc_iso()}
    updated = await db.vault_documents.find_one_and_update(
        {"_id": doc["_id"], "version": doc["version"]},
        {"$push": {"versions": version}, "$set": {
            "version": number, "file_id": version["file_id"], "file_name": version["file_name"],
            "content_type": version["content_type"], "size": version["size"],
            "checksum": version["checksum"], "updated_at": version["uploaded_at"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(409, "The document changed while you were uploading. Please try again.")
    return updated


@router.post("/documents/{doc_id}/versions", status_code=201)
async def upload_version(
    doc_id: str,
    file: UploadFile = File(...),
    note: Optional[str] = Form(None),
    current: UserPublic = Depends(get_current_user),
):
    _require(current, "vault.manage")
    db = get_db()
    doc = await _load(db, doc_id)
    note = _clean_text(note, "Note", 300)
    data, name, ctype = await _read_upload(file)
    storage_id = await _put_bytes(db, data, name, ctype)
    try:
        updated = await _append_version(db, doc, current, {
            "file_id": storage_id, "file_name": name, "content_type": ctype, "size": len(data),
            "checksum": hashlib.sha256(data).hexdigest(), "note": note,
        })
    except Exception:
        await _delete(db, storage_id)
        raise
    await log_activity(db, current, "Uploaded new document version", MODULE, target=doc["title"],
                       meta={"document_id": doc_id, "version": updated["version"]})
    await _notify_founders(db, "New document version",
                           f"{current.name} uploaded version {updated['version']} of “{doc['title']}”.",
                           _doc_link(doc_id), exclude=current.id)
    return _out(updated)


@router.get("/documents/{doc_id}/versions/{number}/download")
async def download_version(doc_id: str, number: int, inline: bool = False,
                           current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    db = get_db()
    doc = await _load(db, doc_id)
    v = _find_version(doc, number)
    chunks, length = await _get_stream(db, v["file_id"])
    if not inline:
        await log_activity(db, current, "Downloaded document version", MODULE, target=doc["title"],
                           meta={"document_id": doc_id, "version": number})
    return _stream_response(chunks, length, v["content_type"], v["file_name"], inline)


@router.post("/documents/{doc_id}/versions/{number}/restore")
async def restore_version(doc_id: str, number: int, current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    doc = await _load(db, doc_id)
    v = _find_version(doc, number)
    if number == doc["version"]:
        raise HTTPException(409, "This is already the current version")
    updated = await _append_version(db, doc, current, {
        "file_id": v["file_id"], "file_name": v["file_name"], "content_type": v["content_type"],
        "size": v["size"], "checksum": v["checksum"], "note": f"Restored from version {number}",
        "restored_from": number,
    })
    await log_activity(db, current, "Restored document version", MODULE, target=doc["title"],
                       meta={"document_id": doc_id, "from_version": number, "version": updated["version"]})
    return _out(updated)


# ------------------------- stats / ops -------------------------

@router.get("/stats")
async def vault_stats(current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.view")
    db = get_db()
    today = _today()
    horizon = (today + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    rows = await db.vault_documents.aggregate([{"$facet": {
        "totals": [{"$group": {"_id": None, "count": {"$sum": 1}, "size": {"$sum": "$size"}}}],
        "by_folder": [{"$group": {"_id": "$folder_id", "count": {"$sum": 1}, "size": {"$sum": "$size"}}}],
        "expired": [{"$match": {"expires_on": {"$ne": None, "$lt": today.isoformat()}}}, {"$count": "n"}],
        "expiring": [{"$match": {"expires_on": {"$gte": today.isoformat(), "$lte": horizon}}}, {"$count": "n"}],
        "files": [{"$unwind": "$versions"},
                  {"$group": {"_id": "$versions.file_id", "size": {"$first": "$versions.size"}}},
                  {"$group": {"_id": None, "size": {"$sum": "$size"}, "count": {"$sum": 1}}}],
    }}]).to_list(1)
    r = rows[0]
    names = {str(f["_id"]): f["name"] async for f in db.vault_folders.find({}, {"name": 1})}
    by_folder = sorted(
        ({"folder_id": g["_id"], "name": names.get(g["_id"], "Unfiled") if g["_id"] else "Unfiled",
          "count": g["count"], "size": g["size"]} for g in r["by_folder"]),
        key=lambda x: (-x["count"], x["name"].lower()),
    )
    totals = r["totals"][0] if r["totals"] else {"count": 0, "size": 0}
    files = r["files"][0] if r["files"] else {"count": 0, "size": 0}
    return {
        "count": totals["count"],
        "total_size": totals["size"],
        "storage_size": files["size"],
        "stored_files": files["count"],
        "by_folder": by_folder,
        "expiring_30d": r["expiring"][0]["n"] if r["expiring"] else 0,
        "expired": r["expired"][0]["n"] if r["expired"] else 0,
    }


@router.post("/reminders/run")
async def run_reminders(current: UserPublic = Depends(get_current_user)):
    _require(current, "vault.manage")
    db = get_db()
    sent = await send_expiry_reminders(db)
    await log_activity(db, current, "Ran vault expiry reminders", MODULE, meta={"sent": sent})
    return {"sent": sent}

from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from bson import ObjectId
from db import get_db
from auth_utils import get_current_user, require_roles
from models import UserPublic
from models_part2 import ChannelIn, MessageIn
from hub_utils import serialize, serialize_many, oid, utc_iso, log_activity, notify

router = APIRouter(prefix="/connect", tags=["connect"])

# Anyone can see and join these kinds; every other kind (group, dm, ...) is members-only.
PUBLIC_KINDS = ("channel", "announcement")


class MembersIn(BaseModel):
    member_ids: List[str] = Field(min_length=1)


class ChannelCreate(ChannelIn):
    """Channel names must be non-blank; surrounding whitespace is trimmed."""
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=500)


class MessageCreate(MessageIn):
    """Blank (whitespace-only) messages are rejected."""
    model_config = ConfigDict(str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=4000)


def _can_view(ch: dict, user_id: str) -> bool:
    return ch.get("kind") in PUBLIC_KINDS or user_id in ch.get("members", [])


async def _visible_channel(db, channel_id: str, current: UserPublic) -> dict:
    ch = await db.channels.find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(404, "Channel not found")
    if not _can_view(ch, current.id):
        raise HTTPException(403, "Not a member")
    return ch


async def _validate_member_ids(db, ids: list[str]) -> list[str]:
    """Dedupe and ensure every id is an existing, active user (422 otherwise)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    if not all(ObjectId.is_valid(i) for i in ids):
        raise HTTPException(422, "Invalid member id")
    found = await db.users.count_documents({
        "_id": {"$in": [ObjectId(i) for i in ids]},
        "status": {"$ne": "deactivated"}, "is_active": {"$ne": False},
    })
    if found != len(ids):
        raise HTTPException(422, "One or more members do not exist")
    return ids


async def _unread_counts(db, channels: list[dict], current_id: str) -> dict[str, int]:
    """Messages from others newer than the user's read cursor, per channel id (single aggregate).
    Without a cursor every message from others counts as unread."""
    if not channels:
        return {}
    ids = [str(c["_id"]) for c in channels]
    cursors = {r["channel_id"]: r["last_read_at"]
               for r in await db.channel_reads.find({"user_id": current_id, "channel_id": {"$in": ids}}).to_list(len(ids))}
    conds = [{"channel_id": cid, "created_at": {"$gt": cursors[cid]}} if cid in cursors else {"channel_id": cid}
             for cid in ids]
    pipe = [
        {"$match": {"sender_id": {"$ne": current_id}, "$or": conds}},
        {"$group": {"_id": "$channel_id", "n": {"$sum": 1}}},
    ]
    return {r["_id"]: r["n"] for r in await db.messages.aggregate(pipe).to_list(len(ids))}


async def _channel_meta(db, doc, current_id: str, unread: int | None = None):
    if unread is None:
        unread = (await _unread_counts(db, [doc], current_id)).get(str(doc["_id"]), 0)
    doc["last_message_at"] = doc.get("last_message_at")
    doc["unread"] = unread
    # For DMs, resolve peer name
    if doc.get("kind") == "dm":
        peer_id = next((m for m in doc.get("members", []) if m != current_id), None)
        if peer_id:
            u = await db.users.find_one({"_id": ObjectId(peer_id)}, {"name": 1, "photo": 1, "role": 1, "online": 1})
            if u:
                doc["display_name"] = u["name"]
                doc["peer_photo"] = u.get("photo")
                doc["peer_role"] = u.get("role")
                doc["peer_online"] = u.get("online", False)
    return doc


@router.get("/channels")
async def list_channels(kind: str | None = None, current: UserPublic = Depends(get_current_user)):
    db = get_db()
    q = {}
    if kind:
        q["kind"] = kind
    # visible: public channels + those the user belongs to
    q["$or"] = [{"kind": {"$in": ["channel", "announcement"]}}, {"members": current.id}]
    docs = await db.channels.find(q).sort("last_message_at", -1).to_list(200)
    unread = await _unread_counts(db, docs, current.id)
    for d in docs:
        await _channel_meta(db, d, current.id, unread.get(str(d["_id"]), 0))
    return serialize_many(docs)


@router.post("/channels", status_code=201)
async def create_channel(payload: ChannelCreate,
                         current: UserPublic = Depends(require_roles("Founder", "Admin", "Manager"))):
    db = get_db()
    if payload.kind == "announcement" and current.role not in ("Founder", "Admin"):
        raise HTTPException(403, "Only Founder or Admin can create announcement channels")
    if payload.kind == "dm":
        raise HTTPException(400, "Use /connect/dm/{peer_id} to start a direct message")
    doc = payload.model_dump()
    doc["description"] = doc.get("description") or None
    added = [m for m in await _validate_member_ids(db, doc["members"]) if m != current.id]
    doc["members"] = [current.id, *added]
    doc["created_by"] = current.id
    doc["created_at"] = utc_iso()
    doc["last_message_at"] = utc_iso()
    res = await db.channels.insert_one(doc)
    doc["_id"] = res.inserted_id
    await _channel_meta(db, doc, current.id)
    await log_activity(db, current, f"Created {doc['kind']}", "WavyGo Connect", target=doc["name"],
                       meta={"members": len(doc["members"])})
    if doc["kind"] not in PUBLIC_KINDS:
        for uid in added:
            await notify(db, uid, "Added to group", f"{current.name} added you to {doc['name']}.",
                         kind="info", link="/wavygo-connect")
    return serialize(doc)


@router.post("/channels/{channel_id}/members")
async def add_members(channel_id: str, payload: MembersIn, current: UserPublic = Depends(get_current_user)):
    """Add members to a private group. Allowed for the group's creator, Founder and Admin."""
    db = get_db()
    ch = await db.channels.find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(404, "Channel not found")
    if ch["kind"] in PUBLIC_KINDS or ch["kind"] == "dm":
        raise HTTPException(400, "Members can only be added to groups")
    if ch.get("created_by") != current.id and current.role not in ("Founder", "Admin"):
        raise HTTPException(403, "Only the group creator, Founder or Admin can add members")
    existing = set(ch.get("members", []))
    added = [m for m in await _validate_member_ids(db, payload.member_ids) if m not in existing]
    if added:
        await db.channels.update_one({"_id": ch["_id"]}, {"$addToSet": {"members": {"$each": added}}})
        await log_activity(db, current, "Added group members", "WavyGo Connect", target=ch["name"],
                           meta={"added": added})
        for uid in added:
            await notify(db, uid, "Added to group", f"{current.name} added you to {ch['name']}.",
                         kind="info", link="/wavygo-connect")
    doc = await db.channels.find_one({"_id": ch["_id"]})
    await _channel_meta(db, doc, current.id)
    return serialize(doc)


HIGH_ROLES = {"Founder", "Admin", "Manager"}
HIGH_DESIGNATION_KEYWORDS = {
    "founder", "ceo", "cto", "coo", "cfo", "chief", "director", "head",
    "president", "vp", "vice president", "manager", "lead", "general manager"
}


def is_high_designation_user(user_dict_or_obj) -> bool:
    role = getattr(user_dict_or_obj, "role", None) or (user_dict_or_obj.get("role") if isinstance(user_dict_or_obj, dict) else None)
    if role in HIGH_ROLES:
        return True
    designation = (
        getattr(user_dict_or_obj, "designation", None) or
        (user_dict_or_obj.get("designation") if isinstance(user_dict_or_obj, dict) else None) or ""
    ).lower()
    return any(k in designation for k in HIGH_DESIGNATION_KEYWORDS)


@router.get("/dm-users", response_model=list[UserPublic])
@router.get("/users", response_model=list[UserPublic])
async def list_dm_eligible_users(current: UserPublic = Depends(get_current_user)):
    """List users that the current user is eligible to DM.
    
    Respective department members can connect with members of their same department
    as well as company leadership / high designation personnel.
    Founders and Admins can connect with anyone across the company.
    """
    db = get_db()
    q = {
        "_id": {"$ne": oid(current.id)},
        "status": {"$ne": "deactivated"},
        "is_active": {"$ne": False},
    }
    docs = await db.users.find(q, {"password_hash": 0}).to_list(500)

    if current.role in ("Founder", "Admin"):
        eligible = docs
    else:
        curr_dept = (current.department or "").strip().lower()
        eligible = []
        for d in docs:
            dept = (d.get("department") or "").strip().lower()
            is_same_dept = bool(curr_dept) and bool(dept) and (dept == curr_dept)
            is_high_desig = is_high_designation_user(d)
            if is_same_dept or is_high_desig:
                eligible.append(d)

    return [
        UserPublic(
            id=str(d["_id"]),
            email=d["email"],
            name=d["name"],
            role=d["role"],
            photo=d.get("photo"),
            online=d.get("online", False),
            phone=d.get("phone"),
            designation=d.get("designation"),
            department=d.get("department"),
            status=d.get("status", "active"),
            is_active=d.get("is_active", True),
        )
        for d in eligible
    ]


@router.post("/dm/{peer_id}", status_code=201)
async def open_dm(peer_id: str, current: UserPublic = Depends(get_current_user)):
    """Get or create a 1-on-1 DM channel with permission enforcement."""
    db = get_db()
    if peer_id == current.id:
        raise HTTPException(400, "Cannot DM yourself")
    peer = await db.users.find_one(
        {"_id": oid(peer_id)},
        {"name": 1, "role": 1, "department": 1, "designation": 1, "status": 1, "is_active": 1}
    )
    if not peer or peer.get("status") == "deactivated" or peer.get("is_active") is False:
        raise HTTPException(404, "User not found")

    # Respective department members connect with same department and high designation personnel
    if current.role not in ("Founder", "Admin"):
        curr_dept = (current.department or "").strip().lower()
        peer_dept = (peer.get("department") or "").strip().lower()
        is_same_dept = bool(curr_dept) and bool(peer_dept) and (curr_dept == peer_dept)
        is_high_desig = is_high_designation_user(peer)
        if not (is_same_dept or is_high_desig):
            raise HTTPException(403, "You can only message members of your department or company leadership.")

    existing = await db.channels.find_one({
        "kind": "dm",
        "members": {"$all": [current.id, peer_id], "$size": 2},
    })
    if existing:
        await _channel_meta(db, existing, current.id)
        return serialize(existing)
    doc = {
        "name": peer["name"],
        "kind": "dm",
        "description": None,
        "members": [current.id, peer_id],
        "created_by": current.id,
        "created_at": utc_iso(),
        "last_message_at": utc_iso(),
    }
    res = await db.channels.insert_one(doc)
    doc["_id"] = res.inserted_id
    await _channel_meta(db, doc, current.id)
    return serialize(doc)


@router.get("/channels/{channel_id}/messages")
async def list_messages(channel_id: str, limit: int = Query(100, ge=1, le=500),
                        current: UserPublic = Depends(get_current_user)):
    db = get_db()
    await _visible_channel(db, channel_id, current)
    docs = await db.messages.find({"channel_id": channel_id}).sort("created_at", -1).to_list(limit)
    docs.reverse()
    return serialize_many(docs)


@router.post("/channels/{channel_id}/messages", status_code=201)
async def send_message(channel_id: str, payload: MessageCreate, current: UserPublic = Depends(get_current_user)):
    db = get_db()
    ch = await _visible_channel(db, channel_id, current)
    if ch["kind"] == "announcement" and current.role not in ("Founder", "Admin"):
        raise HTTPException(403, "Only Founder or Admin can post in announcement channels")
    doc = {
        "channel_id": channel_id,
        "channel_name": ch["name"],
        "sender_id": current.id,
        "sender_name": current.name,
        "sender_role": current.role,
        "sender_photo": current.photo,
        "body": payload.body,
        "attachments": payload.attachments,
        "created_at": utc_iso(),
    }
    res = await db.messages.insert_one(doc)
    doc["_id"] = res.inserted_id
    await db.channels.update_one({"_id": oid(channel_id)}, {"$set": {"last_message_at": doc["created_at"], "last_body": payload.body[:120]}})
    if ch["kind"] == "announcement":
        await notify(db, None, f"Announcement · {ch['name']}", payload.body[:180], kind="info", link="/wavygo-connect")
    return serialize(doc)


@router.post("/channels/{channel_id}/join")
async def join_channel(channel_id: str, current: UserPublic = Depends(get_current_user)):
    db = get_db()
    ch = await db.channels.find_one({"_id": oid(channel_id)})
    if not ch:
        raise HTTPException(404, "Channel not found")
    if ch["kind"] not in PUBLIC_KINDS:
        raise HTTPException(403, "Private groups and direct messages cannot be joined")
    if current.id not in ch.get("members", []):
        await db.channels.update_one({"_id": oid(channel_id)}, {"$addToSet": {"members": current.id}})
        await log_activity(db, current, "Joined channel", "WavyGo Connect", target=ch["name"])
    return {"ok": True}


@router.post("/channels/{channel_id}/read")
async def mark_read(channel_id: str, current: UserPublic = Depends(get_current_user)):
    """Move the user's read cursor for this channel to now (clears its unread count)."""
    db = get_db()
    await _visible_channel(db, channel_id, current)
    now = utc_iso()
    await db.channel_reads.update_one({"channel_id": channel_id, "user_id": current.id},
                                      {"$set": {"last_read_at": now}}, upsert=True)
    return {"ok": True, "last_read_at": now}

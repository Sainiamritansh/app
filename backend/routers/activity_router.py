from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from db import get_db
from auth_utils import get_current_user
from models import UserPublic


async def _dept_ids(db, department):
    if not department:
        return []
    return [str(u["_id"]) async for u in db.users.find({"department": department}, {"_id": 1})]

router = APIRouter(prefix="/activity", tags=["activity"])


def _serialize(doc):
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "user_name": doc.get("user_name"),
        "user_role": doc.get("user_role"),
        "action": doc.get("action"),
        "module": doc.get("module"),
        "target": doc.get("target"),
        "created_at": doc.get("created_at"),
    }


def _scope_check(current: UserPublic):
    if current.role in ("Employee", "Intern"):
        raise HTTPException(status_code=403, detail="Activity logs are not available for your role")


@router.get("/modules")
async def list_modules(current: UserPublic = Depends(get_current_user)):
    """Distinct module names in the caller's visible activity, for the module filter."""
    db = get_db()
    _scope_check(current)
    q = {}
    if current.role == "Manager":
        q["user_id"] = {"$in": await _dept_ids(db, current.department)}
    return sorted(m for m in await db.activity_logs.distinct("module", q) if m)


@router.get("")
async def list_activity(
    limit: int = Query(50, ge=1, le=200),
    module: str | None = None,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()
    _scope_check(current)
    q = {}
    if module:
        q["module"] = module
    if current.role == "Manager":
        q["user_id"] = {"$in": await _dept_ids(db, current.department)}
    docs = await db.activity_logs.find(q).sort("created_at", -1).to_list(limit)
    return [_serialize(d) for d in docs]

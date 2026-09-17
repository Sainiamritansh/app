from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from bson import ObjectId

from db import get_db, utc_now
from models import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    RefreshRequest,
    UserPublic,
)
from auth_utils import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    require_roles,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_public(doc) -> UserPublic:
    return UserPublic(
        id=str(doc["_id"]),
        email=doc["email"],
        name=doc["name"],
        role=doc["role"],
        photo=doc.get("photo"),
        online=doc.get("online", False),
        phone=doc.get("phone"),
        designation=doc.get("designation"),
        department=doc.get("department"),
        status=doc.get("status", "active"),
        is_active=doc.get("is_active", True),
    )


async def _log_activity(
    db,
    user,
    action: str,
    module: str = "Auth",
    target: str | None = None,
):
    await db.activity_logs.insert_one({
        "user_id": user["id"] if isinstance(user, dict) else user.id,
        "user_name": user["name"] if isinstance(user, dict) else user.name,
        "user_role": user["role"] if isinstance(user, dict) else user.role,
        "action": action,
        "module": module,
        "target": target,
        "created_at": utc_now().isoformat(),
    })


# ============================================================
# LOGIN
# ============================================================

@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request):
    db = get_db()

    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await db.users.update_one({"_id": user["_id"]}, {"$set": {"online": True, "last_login_at": utc_now().isoformat()}})

    uid = str(user["_id"])

    access = create_access_token(
        uid,
        user["email"],
        user["role"],
    )

    refresh, jti = create_refresh_token(uid)

    # --------------------------------------------------------
    # 4. Create login session
    # --------------------------------------------------------

    session_doc = {
        "user_id": uid,
        "refresh_token_id": jti,
        "user_agent": request.headers.get("user-agent"),
        "ip": request.client.host if request.client else None,
        "created_at": now.isoformat(),
        "last_used_at": now.isoformat(),
        "revoked": False,
    }

    session_result = await db.sessions.insert_one(session_doc)

    session_id = str(session_result.inserted_id)

    # --------------------------------------------------------
    # 5. AUTOMATIC ATTENDANCE CHECK-IN
    # --------------------------------------------------------

    today = now.date().isoformat()

    existing_attendance = await db.attendance.find_one({
        "employee_id": uid,
        "date": today,
    })

    # Create only one attendance record per employee per day
    if not existing_attendance:

        await db.attendance.insert_one({
            "employee_id": uid,
            "employee_name": user["name"],
            "date": today,
            "status": "present",
            "check_in": now.isoformat(),
            "check_out": None,
            "session_id": session_id,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })

    # --------------------------------------------------------
    # 6. Prepare public user
    # --------------------------------------------------------

    public = _to_public({
        **user,
        "online": True,
    })

    # --------------------------------------------------------
    # 7. Log activity
    # --------------------------------------------------------

    await _log_activity(
        db,
        public,
        "Signed in",
    )

    # --------------------------------------------------------
    # 8. Return login response
    # --------------------------------------------------------

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=public,
    )


# ============================================================
# REGISTER
# ============================================================

@router.post("/register", response_model=TokenResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin")
    ),
):
    if payload.role == "Founder":
        raise HTTPException(
            status_code=403,
            detail="Cannot create another Founder",
        )

    if payload.role == "Admin" and current.role != "Founder":
        raise HTTPException(
            status_code=403,
            detail="Only the Founder can create an Admin",
        )

    db = get_db()

    email = payload.email.lower().strip()

    # Check duplicate email
    if await db.users.find_one({"email": email}):
        raise HTTPException(
            status_code=409,
            detail="Email already registered",
        )

    now = utc_now()

    # --------------------------------------------------------
    # Create user
    # --------------------------------------------------------

    doc = {
        "email": email,
        "name": payload.name.strip(),
        "role": payload.role,
        "password_hash": hash_password(payload.password),
        "online": True,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    res = await db.users.insert_one(doc)

    doc["_id"] = res.inserted_id

    uid = str(res.inserted_id)

    # --------------------------------------------------------
    # Create tokens
    # --------------------------------------------------------

    access = create_access_token(
        uid,
        email,
        payload.role,
    )

    refresh, jti = create_refresh_token(uid)

    # --------------------------------------------------------
    # Create session for newly registered user
    # --------------------------------------------------------

    await db.sessions.insert_one({
        "user_id": uid,
        "refresh_token_id": jti,
        "user_agent": request.headers.get("user-agent"),
        "ip": request.client.host if request.client else None,
        "created_at": now.isoformat(),
        "last_used_at": now.isoformat(),
        "revoked": False,
    })

    # --------------------------------------------------------
    # Automatic attendance check-in for registration login
    # --------------------------------------------------------

    today = now.date().isoformat()

    existing_attendance = await db.attendance.find_one({
        "employee_id": uid,
        "date": today,
    })

    if not existing_attendance:

        await db.attendance.insert_one({
            "employee_id": uid,
            "employee_name": payload.name.strip(),
            "date": today,
            "status": "present",
            "check_in": now.isoformat(),
            "check_out": None,
            "session_id": jti,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })

    public = _to_public(doc)

    await _log_activity(
        db,
        public,
        "Account created",
    )

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        user=public,
    )


# ============================================================
# REFRESH TOKEN
# ============================================================

@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest):
    db = get_db()

    try:
        data = decode_token(payload.refresh_token)

        if data.get("type") != "refresh":
            raise HTTPException(
                status_code=401,
                detail="Invalid refresh token",
            )

    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired refresh token",
        )

    uid = data["sub"]

    user = await db.users.find_one({
        "_id": ObjectId(uid)
    })

    if not user:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    access = create_access_token(
        uid,
        user["email"],
        user["role"],
    )

    new_refresh, _ = create_refresh_token(uid)

    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        user=_to_public(user),
    )


# ============================================================
# LOGOUT
# ============================================================

@router.post("/logout")
async def logout(
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    now = utc_now()

    today = now.date().isoformat()

    # --------------------------------------------------------
    # 1. AUTOMATIC ATTENDANCE CHECK-OUT
    # --------------------------------------------------------
    # NOTE: no "check_out": None filter here — always overwrite
    # with the latest logout time, in case of multiple
    # login/logout cycles in the same day.

    await db.attendance.update_one(
        {
            "employee_id": current.id,
            "date": today,
        },
        {
            "$set": {
                "check_out": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        },
    )

    # --------------------------------------------------------
    # 2. Mark user offline
    # --------------------------------------------------------

    await db.users.update_one(
        {
            "_id": ObjectId(current.id)
        },
        {
            "$set": {
                "online": False
            }
        },
    )

    # --------------------------------------------------------
    # 3. Log activity
    # --------------------------------------------------------

    await _log_activity(
        db,
        current,
        "Signed out",
    )

    return {
        "ok": True
    }


# ============================================================
# CURRENT USER
# ============================================================

@router.get("/me", response_model=UserPublic)
async def me(
    current: UserPublic = Depends(get_current_user),
):
    return current


# ============================================================
# ATTENDANCE (FOUNDER VIEW)
# ============================================================

@router.get("/attendance", response_model=list[dict])
async def list_attendance(
    current: UserPublic = Depends(require_roles("Founder")),
):
    db = get_db()

    records = await db.attendance.find().sort("date", -1).to_list(500)

    return [
        {
            "employee": r["employee_name"],
            "date": r["date"],
            "check_in": r.get("check_in"),
            "check_out": r.get("check_out"),
        }
        for r in records
    ]
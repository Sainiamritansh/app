from __future__ import annotations
import secrets
import string
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId

from db import get_db
from auth_utils import get_current_user, require_roles, hash_password
from models import UserPublic, EmployeeAdminUpdateRequest
from models_part2 import (
    DepartmentIn,
    EmployeeInviteIn,
    AttendanceIn,
    LeaveIn,
    PerformanceIn
)
from hub_utils import serialize, serialize_many, oid, utc_iso, log_activity, notify


router = APIRouter(prefix="/employees", tags=["employees"])


async def _dept_ids(db, department):
    """Return list of user ids (str) in the given department."""
    if not department:
        return []

    return [
        str(u["_id"])
        async for u in db.users.find(
            {"department": department},
            {"_id": 1},
        )
    ]


def _gen_temp_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "Wg" + "".join(
        secrets.choice(alphabet) for _ in range(length)
    )


# -------------------------------------------------------------------
# Employees
# -------------------------------------------------------------------

@router.get("")
async def list_employees(
    q: str | None = None,
    department: str | None = None,
    role: str | None = None,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin", "Manager")
    ),
):
    db = get_db()

    query = {}

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"designation": {"$regex": q, "$options": "i"}},
        ]

    if department:
        query["department"] = department

    if role:
        query["role"] = role

    # Managers only see their own department
    if current.role == "Manager":
        query["department"] = current.department

    docs = await db.users.find(
        query,
        {"password_hash": 0},
    ).sort(
        "created_at",
        -1,
    ).to_list(500)

    return serialize_many(
        docs,
        drop=("password_hash",),
    )


@router.post("/invite", status_code=201)
async def invite_employee(
    payload: EmployeeInviteIn,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin")
    ),
):
    db = get_db()

    # Founder cannot be created from employee section
    if payload.role == "Founder":
        raise HTTPException(
            403,
            "Cannot create another Founder",
        )

    # Only Founder can create Admin
    if payload.role == "Admin" and current.role != "Founder":
        raise HTTPException(
            403,
            "Only the Founder can create an Admin",
        )

    email = payload.email.lower().strip()

    # Prevent duplicate accounts
    if await db.users.find_one({"email": email}):
        raise HTTPException(
            409,
            "Email already exists",
        )

    default_pw = "Wavygo@2026"

    doc = {
        "email": email,
        "name": payload.name.strip(),
        "role": payload.role,
        "designation": payload.designation,
        "department": payload.department,
        "phone": payload.phone,
        "password_hash": hash_password(default_pw),
        "online": False,
        "created_at": utc_iso(),
        "updated_at": utc_iso(),
    }

    res = await db.users.insert_one(doc)

    doc["_id"] = res.inserted_id

    await log_activity(
        db,
        current,
        "Invited employee",
        "Employees",
        target=payload.name,
    )

    managers = await db.users.find(
        {"role": {"$in": ["Founder", "Admin"]}},
        {"_id": 1},
    ).to_list(50)

    for m in managers:
        await notify(
            db,
            str(m["_id"]),
            "New teammate joined",
            f"{payload.name} was invited by {current.name} as {payload.role}.",
            kind="success",
            link="/employees",
        )

    return {
        **serialize(doc),
        "temp_password": default_pw,
    }


# -------------------------------------------------------------------
# Edit Employee
# -------------------------------------------------------------------

@router.patch("/{employee_id}")
async def update_employee(
    employee_id: str,
    payload: EmployeeAdminUpdateRequest,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin")
    ),
):
    """
    Allow authorized Founder/Admin users to edit an employee profile.

    Editable fields:
    - name
    - email
    - role
    - phone
    - designation
    - department
    - photo

    Password is intentionally NOT editable through this endpoint.
    Password changes use the reset-password endpoint.
    """

    db = get_db()

    # ---------------------------------------------------------------
    # 1. Validate employee ID
    # ---------------------------------------------------------------

    try:
        employee_oid = oid(employee_id)
    except Exception:
        raise HTTPException(
            400,
            "Invalid employee ID",
        )

    # ---------------------------------------------------------------
    # 2. Find employee
    # ---------------------------------------------------------------

    target = await db.users.find_one(
        {"_id": employee_oid}
    )

    if not target:
        raise HTTPException(
            404,
            "Employee not found",
        )

    # ---------------------------------------------------------------
    # 3. Protect Founder account
    # ---------------------------------------------------------------

    if target.get("role") == "Founder":
        raise HTTPException(
            403,
            "Cannot edit the Founder",
        )

    # ---------------------------------------------------------------
    # 4. Get only fields actually provided by frontend
    # ---------------------------------------------------------------

    update_data = payload.model_dump(
        exclude_unset=True
    )

    if not update_data:
        raise HTTPException(
            400,
            "No fields provided for update",
        )

    # ---------------------------------------------------------------
    # 5. Email validation + duplicate email prevention
    # ---------------------------------------------------------------

    if "email" in update_data:

        email = update_data["email"]

        if email:
            email = str(email).lower().strip()

            # Do not allow another user to have this email
            existing = await db.users.find_one(
                {
                    "email": email,
                    "_id": {
                        "$ne": employee_oid
                    },
                }
            )

            if existing:
                raise HTTPException(
                    409,
                    "Email already exists for another user",
                )

            update_data["email"] = email

    # ---------------------------------------------------------------
    # 6. Role restrictions
    # ---------------------------------------------------------------

    new_role = update_data.get("role")

    # Nobody can assign Founder through this endpoint
    if new_role == "Founder":
        raise HTTPException(
            403,
            "Cannot assign the Founder role",
        )

    # Only Founder can assign Admin
    if (
        new_role == "Admin"
        and current.role != "Founder"
    ):
        raise HTTPException(
            403,
            "Only the Founder can assign the Admin role",
        )

    # ---------------------------------------------------------------
    # 7. Make sure password can NEVER be changed here
    # ---------------------------------------------------------------

    update_data.pop(
        "password_hash",
        None,
    )

    # These IDs should never be modified by frontend
    update_data.pop(
        "id",
        None,
    )

    update_data.pop(
        "_id",
        None,
    )

    # ---------------------------------------------------------------
    # 8. Add updated timestamp
    # ---------------------------------------------------------------

    update_data["updated_at"] = utc_iso()

    # ---------------------------------------------------------------
    # 9. Update MongoDB
    # ---------------------------------------------------------------

    result = await db.users.update_one(
        {"_id": employee_oid},
        {
            "$set": update_data
        },
    )

    if result.matched_count == 0:
        raise HTTPException(
            404,
            "Employee not found",
        )

    # ---------------------------------------------------------------
    # 10. Get updated employee
    # ---------------------------------------------------------------

    updated_employee = await db.users.find_one(
        {"_id": employee_oid},
        {"password_hash": 0},
    )

    # ---------------------------------------------------------------
    # 11. Record activity log
    # ---------------------------------------------------------------

    await log_activity(
        db,
        current,
        "Updated employee",
        "Employees",
        target=updated_employee["name"],
        meta={
            "employee_id": employee_id,
            "updated_fields": list(
                update_data.keys()
            ),
        },
    )

    # ---------------------------------------------------------------
    # 12. Return updated employee
    # ---------------------------------------------------------------

    return serialize(updated_employee)


# -------------------------------------------------------------------
# Reset Employee Password
# -------------------------------------------------------------------

@router.post("/{employee_id}/reset-password")
async def reset_employee_password(
    employee_id: str,
    payload: dict | None = None,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin")
    ),
):
    db = get_db()

    target = await db.users.find_one(
        {"_id": oid(employee_id)}
    )

    if not target:
        raise HTTPException(
            404,
            "Not found",
        )

    if target.get("role") == "Founder":
        raise HTTPException(
            403,
            "Cannot reset the Founder password from here",
        )

    new_password = (
        (payload or {}).get("new_password")
        or _gen_temp_password()
    )

    await db.users.update_one(
        {"_id": oid(employee_id)},
        {
            "$set": {
                "password_hash": hash_password(
                    new_password
                ),
                "updated_at": utc_iso(),
            }
        },
    )

    await log_activity(
        db,
        current,
        "Reset password",
        "Employees",
        target=target["name"],
    )

    await notify(
        db,
        str(target["_id"]),
        "Your password was reset",
        f"{current.name} reset your password. "
        "Please sign in with the new temporary password "
        "and update it if allowed.",
        kind="warning",
        link="/settings",
    )

    return {
        "ok": True,
        "temp_password": new_password,
    }


# -------------------------------------------------------------------
# Departments
# -------------------------------------------------------------------

@router.get("/departments/list")
async def list_departments(
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    docs = await db.departments.find().sort(
        "name",
        1,
    ).to_list(200)

    out = []

    for d in docs:
        head_name = None

        if d.get("head_id"):
            try:
                u = await db.users.find_one(
                    {
                        "_id": ObjectId(
                            d["head_id"]
                        )
                    },
                    {
                        "name": 1
                    },
                )

                head_name = (
                    u["name"]
                    if u
                    else None
                )

            except Exception:
                pass

        headcount = await db.users.count_documents(
            {
                "department": d["name"]
            }
        )

        out.append(
            {
                **serialize(d),
                "head_name": head_name,
                "headcount": headcount,
            }
        )

    return out


@router.post("/departments/list", status_code=201)
async def create_department(
    payload: DepartmentIn,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin")
    ),
):
    db = get_db()

    doc = payload.model_dump()
    doc["created_at"] = utc_iso()

    res = await db.departments.insert_one(doc)

    doc["_id"] = res.inserted_id

    await log_activity(
        db,
        current,
        "Created department",
        "Employees",
        target=doc["name"],
    )

    return serialize(doc)


# -------------------------------------------------------------------
# Attendance
# -------------------------------------------------------------------

@router.get("/attendance/records")
async def list_attendance(
    employee_id: str | None = None,
    date: str | None = None,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    q = {}

    if date:
        q["date"] = date

    if current.role in ("Employee", "Intern"):
        q["employee_id"] = current.id

    elif current.role == "Manager":
        q["employee_id"] = {
            "$in": await _dept_ids(
                db,
                current.department,
            )
        }

    elif employee_id:
        q["employee_id"] = employee_id

    docs = await db.attendance.find(q).sort(
        "date",
        -1,
    ).to_list(500)

    return serialize_many(docs)


@router.post("/attendance/records", status_code=201)
async def add_attendance(
    payload: AttendanceIn,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    doc = payload.model_dump()
    if current.role in ("Employee", "Intern") and doc["employee_id"] != current.id:
        raise HTTPException(403, "You can only mark your own attendance")
    if current.role == "Manager":
        tgt = await db.users.find_one(
            {
                "_id": oid(
                    doc["employee_id"]
                )
            }
        )

        if (
            not tgt
            or tgt.get("department")
            != current.department
        ):
            raise HTTPException(
                403,
                "Managers can only mark attendance for their department",
            )

    doc["created_at"] = utc_iso()

    await db.attendance.update_one(
        {
            "employee_id": doc["employee_id"],
            "date": doc["date"],
        },
        {
            "$set": doc
        },
        upsert=True,
    )

    emp = await db.users.find_one(
        {
            "_id": oid(
                doc["employee_id"]
            )
        },
        {
            "name": 1
        },
    )

    await log_activity(
        db,
        current,
        f"Attendance · {doc['status']}",
        "Employees",
        target=f"{emp['name'] if emp else '—'} · {doc['date']}",
    )

    return {
        "ok": True
    }


# -------------------------------------------------------------------
# Leave
# -------------------------------------------------------------------

@router.get("/leave/requests")
async def list_leave(
    status: str | None = None,
    employee_id: str | None = None,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    q = {}

    if status:
        q["status"] = status

    if current.role in ("Employee", "Intern"):
        q["employee_id"] = current.id

    elif current.role == "Manager":
        q["employee_id"] = {
            "$in": await _dept_ids(
                db,
                current.department,
            )
        }

    elif employee_id:
        q["employee_id"] = employee_id

    docs = await db.leave_requests.find(q).sort(
        "created_at",
        -1,
    ).to_list(500)

    out = []

    for d in docs:
        emp = (
            await db.users.find_one(
                {
                    "_id": ObjectId(
                        d["employee_id"]
                    )
                },
                {
                    "name": 1,
                    "photo": 1,
                },
            )
            if d.get("employee_id")
            else None
        )

        out.append(
            {
                **serialize(d),
                "employee_name": (
                    emp["name"]
                    if emp
                    else None
                ),
                "employee_photo": (
                    emp.get("photo")
                    if emp
                    else None
                ),
            }
        )

    return out


@router.post("/leave/requests", status_code=201)
async def create_leave(
    payload: LeaveIn,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    doc = payload.model_dump()

    if (
        current.role in ("Employee", "Intern")
        and doc["employee_id"] != current.id
    ):
        raise HTTPException(
            403,
            "You can only request leave for yourself",
        )

    if current.role == "Manager":
        tgt = await db.users.find_one(
            {
                "_id": oid(
                    doc["employee_id"]
                )
            }
        )

        if (
            not tgt
            or tgt.get("department")
            != current.department
        ):
            raise HTTPException(
                403,
                "Managers can only request leave for their department",
            )

    doc["created_at"] = utc_iso()
    doc["updated_at"] = utc_iso()

    res = await db.leave_requests.insert_one(doc)

    doc["_id"] = res.inserted_id

    emp = await db.users.find_one(
        {
            "_id": oid(
                doc["employee_id"]
            )
        },
        {
            "name": 1
        },
    )

    await log_activity(
        db,
        current,
        "Leave requested",
        "Employees",
        target=(
            f"{emp['name'] if emp else '—'} · "
            f"{doc['from_date']} → {doc['to_date']}"
        ),
    )

    approvers = await db.users.find(
        {"role": {"$in": ["Founder", "Admin", "Manager"]}},
        {"_id": 1},
    ).to_list(50)

    for a in approvers:
        await notify(
            db,
            str(a["_id"]),
            "Leave request",
            (
                f"{emp['name'] if emp else 'Employee'} "
                f"requested {doc['kind']} leave from "
                f"{doc['from_date']} to {doc['to_date']}."
            ),
            kind="warning",
            link="/employees",
        )

    return serialize(doc)


@router.patch("/leave/requests/{leave_id}")
async def update_leave(
    leave_id: str,
    payload: dict,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin", "Manager")
    ),
):
    db = get_db()

    status = payload.get("status")

    if status not in {
        "pending",
        "approved",
        "rejected",
    }:
        raise HTTPException(
            400,
            "Invalid status",
        )

    doc = await db.leave_requests.find_one(
        {
            "_id": oid(leave_id)
        }
    )

    if not doc:
        raise HTTPException(
            404,
            "Not found",
        )

    if current.role == "Manager":
        emp = (
            await db.users.find_one(
                {
                    "_id": ObjectId(
                        doc["employee_id"]
                    )
                }
            )
            if doc.get("employee_id")
            else None
        )

        if (
            not emp
            or emp.get("department")
            != current.department
        ):
            raise HTTPException(
                403,
                "Managers can only action leave for their department",
            )

    await db.leave_requests.update_one(
        {
            "_id": oid(leave_id)
        },
        {
            "$set": {
                "status": status,
                "updated_at": utc_iso(),
            }
        },
    )

    doc = await db.leave_requests.find_one(
        {
            "_id": oid(leave_id)
        }
    )

    emp = (
        await db.users.find_one(
            {
                "_id": ObjectId(
                    doc["employee_id"]
                )
            },
            {
                "name": 1
            },
        )
        if doc.get("employee_id")
        else None
    )

    await log_activity(
        db,
        current,
        f"Leave {status}",
        "Employees",
        target=(
            emp["name"]
            if emp
            else None
        ),
    )

    if emp and doc.get("employee_id"):
        await notify(
            db,
            doc["employee_id"],
            f"Your leave was {status}",
            (
                f"{current.name} {status} "
                f"your leave request "
                f"{doc['from_date']} → {doc['to_date']}"
            ),
            kind=(
                "success"
                if status == "approved"
                else "warning"
            ),
            link="/employees",
        )

    return serialize(doc)


# -------------------------------------------------------------------
# Performance
# -------------------------------------------------------------------

@router.get("/performance/reviews")
async def list_performance(
    employee_id: str | None = None,
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    q = {}

    if current.role in ("Employee", "Intern"):
        q["employee_id"] = current.id

    elif current.role == "Manager":
        q["employee_id"] = {
            "$in": await _dept_ids(
                db,
                current.department,
            )
        }

    elif employee_id:
        q["employee_id"] = employee_id

    docs = await db.performance_reviews.find(q).sort(
        "created_at",
        -1,
    ).to_list(300)

    out = []

    for d in docs:
        emp = (
            await db.users.find_one(
                {
                    "_id": ObjectId(
                        d["employee_id"]
                    )
                },
                {
                    "name": 1,
                    "designation": 1,
                    "photo": 1,
                },
            )
            if d.get("employee_id")
            else None
        )

        out.append(
            {
                **serialize(d),
                "employee_name": (
                    emp["name"]
                    if emp
                    else None
                ),
                "employee_designation": (
                    emp.get("designation")
                    if emp
                    else None
                ),
                "employee_photo": (
                    emp.get("photo")
                    if emp
                    else None
                ),
            }
        )

    return out


@router.post("/performance/reviews", status_code=201)
async def create_performance(
    payload: PerformanceIn,
    current: UserPublic = Depends(
        require_roles("Founder", "Admin", "Manager")
    ),
):
    db = get_db()

    doc = payload.model_dump()

    if current.role == "Manager":
        tgt = await db.users.find_one(
            {
                "_id": oid(
                    doc["employee_id"]
                )
            }
        )

        if (
            not tgt
            or tgt.get("department")
            != current.department
        ):
            raise HTTPException(
                403,
                "Managers can only review their department",
            )

    doc["created_at"] = utc_iso()
    doc["reviewer_id"] = current.id
    doc["reviewer_name"] = current.name

    res = await db.performance_reviews.insert_one(doc)

    doc["_id"] = res.inserted_id

    emp = await db.users.find_one(
        {
            "_id": oid(
                doc["employee_id"]
            )
        },
        {
            "name": 1
        },
    )

    await log_activity(
        db,
        current,
        "Performance review recorded",
        "Employees",
        target=(
            f"{emp['name'] if emp else '—'} · "
            f"{doc['period']}"
        ),
    )

    if emp and doc.get("employee_id"):
        await notify(
            db,
            doc["employee_id"],
            "Performance review saved",
            (
                f"{current.name} saved your "
                f"{doc['period']} review."
            ),
            kind="info",
            link="/employees",
        )

    return serialize(doc)


# -------------------------------------------------------------------
# Overview Stats
# -------------------------------------------------------------------

@router.get("/stats/overview")
async def employees_overview(
    current: UserPublic = Depends(get_current_user),
):
    db = get_db()

    if current.role in ("Employee", "Intern"):

        my_leave = await db.leave_requests.count_documents(
            {
                "employee_id": current.id,
                "status": "pending",
            }
        )

        return {
            "total": 1,
            "online": (
                1
                if current.online
                else 0
            ),
            "by_role": [
                {
                    "role": current.role,
                    "count": 1,
                }
            ],
            "pending_leave": my_leave,
            "departments": 0,
        }

    total = await db.users.count_documents({})

    online = await db.users.count_documents(
        {
            "online": True
        }
    )

    by_role = {}

    async for u in db.users.find(
        {},
        {
            "role": 1
        },
    ):
        role = u.get(
            "role",
            "Employee",
        )

        by_role[role] = (
            by_role.get(role, 0) + 1
        )

    pending_leave = await db.leave_requests.count_documents(
        {
            "status": "pending"
        }
    )

    return {
        "total": total,
        "online": online,
        "by_role": [
            {
                "role": k,
                "count": v,
            }
            for k, v in by_role.items()
        ],
        "pending_leave": pending_leave,
        "departments": await db.departments.count_documents({}),
    }
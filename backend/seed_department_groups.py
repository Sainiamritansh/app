"""
Run this ONCE after adding dept_groups.py and wiring the call-sites into
employees_router.py, to backfill your existing 4-5 departments and enroll
all current employees into their department's group.

Usage (from the backend folder, with venv active):
    python seed_department_groups.py
"""
import asyncio
from db import get_db
from dept_groups import get_or_create_department_channel, add_member_to_department_channel


async def run():
    db = get_db()

    dept_names = set()

    # Departments from the departments collection
    async for d in db.departments.find({}, {"name": 1}):
        if d.get("name"):
            dept_names.add(d["name"].strip())

    # Also catch any department string set directly on a user that
    # doesn't have a matching row in db.departments (covers data drift)
    async for u in db.users.find({}, {"department": 1}):
        if u.get("department"):
            dept_names.add(u["department"].strip())

    print(f"Found {len(dept_names)} distinct departments: {sorted(dept_names)}")

    for name in dept_names:
        await get_or_create_department_channel(db, name)
    print("Group channels created/verified for all departments.")

    enrolled = 0
    async for u in db.users.find({}, {"department": 1}):
        if u.get("department"):
            await add_member_to_department_channel(db, str(u["_id"]), u["department"])
            enrolled += 1

    print(f"Enrolled {enrolled} employees into their department groups.")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(run())

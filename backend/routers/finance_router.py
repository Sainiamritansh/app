"""Finance module API (mounted at /api/finance): invoices, vendor payouts, statements.

Every figure is derived from the Marketplace collections (`bookings`, `vendors`,
`customers`, `cities`) plus Finance's own collections:

  finance_settings   single doc `_id: "default"` - commission %, GST %, invoice prefix, company details
  finance_counters   `_id: "invoice:<FY>"` - atomic invoice sequence per financial year (April-March)
  invoices           one live (non-void) invoice per booking, enforced by a unique index on
                     `active_booking_id` (removed when an invoice is voided so it can be re-issued)
  payouts            one doc per vendor per payout batch (`batch_id`), status pending / paid
  payout_items       one doc per booking included in a payout - unique `booking_id` guarantees a
                     booking can be paid out at most once

Rules
- Revenue counts bookings with status confirmed, active or completed (never pending / cancelled),
  the same rule as the Founder dashboard. Bookings are dated by `created_at` (ISO string or date).
- Month / day boundaries use the company timezone, Asia/Kolkata.
- Vendor earnings = booking amount x (1 - commission %). Only completed bookings are paid out, so a
  booking that may still be cancelled never lands in a payout.
- Everything is Founder-only via `permissions.can` (finance.view / finance.manage).
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError

from auth_utils import get_current_user
from db import get_db
from hub_utils import log_activity, notify, oid, serialize, utc_iso
from models import UserPublic
from permissions import can

router = APIRouter(prefix="/finance", tags=["finance"])

IST = ZoneInfo("Asia/Kolkata")
REVENUE_STATUSES = ["confirmed", "active", "completed"]
PAYABLE_STATUS = "completed"
MODULE = "Finance"
LINK = "/finance"
MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
PAYMENT_METHODS = ("upi", "card", "cash", "bank_transfer", "cheque", "other")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]{3}$")

DEFAULT_SETTINGS = {
    "commission_pct": 20.0,
    "gst_pct": 18.0,
    "invoice_prefix": "WG-INV",
    "prices_include_gst": True,
    "company_name": "WAVYGO MOBILITY SERVICES PRIVATE LIMITED",
    "company_gstin": None,
    "company_state": None,
    "billing_address": None,
}


# ------------------------------------------------------------------ RBAC

def _perm(action: str):
    async def dep(current: UserPublic = Depends(get_current_user)) -> UserPublic:
        if not can(current.role, action):
            raise HTTPException(403, "You do not have access to Finance")
        return current
    return dep


Viewer = Depends(_perm("finance.view"))
Manager = Depends(_perm("finance.manage"))


# ------------------------------------------------------------------ indexes

_indexes_ready = False


async def ensure_indexes(db) -> None:
    """Idempotent. Also run lazily before the first Finance write, so correctness never depends
    on the startup hook."""
    global _indexes_ready
    await db.invoices.create_index("active_booking_id", unique=True,
                                   partialFilterExpression={"active_booking_id": {"$exists": True}})
    await db.invoices.create_index("number", unique=True,
                                   partialFilterExpression={"number": {"$type": "string"}})
    await db.invoices.create_index([("invoice_date", -1), ("created_at", -1)])
    await db.invoices.create_index("status")
    await db.invoices.create_index("customer_id")
    await db.payout_items.create_index("booking_id", unique=True)
    await db.payout_items.create_index("payout_id")
    await db.payouts.create_index([("created_at", -1)])
    await db.payouts.create_index("batch_id")
    _indexes_ready = True


async def _ready(db):
    if not _indexes_ready:
        await ensure_indexes(db)


# ------------------------------------------------------------------ helpers

def _money(x) -> float:
    return float(Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _as_utc(value) -> Optional[datetime]:
    """created_at may be an ISO string or a BSON datetime (naive = UTC)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _today() -> date:
    return datetime.now(IST).date()


def _ist_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=IST).astimezone(timezone.utc)


def _month_start(ym: str) -> date:
    return date(int(ym[:4]), int(ym[5:7]), 1)


def _add_months(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def _month_range(first: str, last: str) -> tuple[datetime, datetime]:
    return _ist_midnight(_month_start(first)), _ist_midnight(_month_start(_add_months(last, 1)))


def _month_label(ym: str) -> str:
    return _month_start(ym).strftime("%b %Y")


def _current_month() -> str:
    return _today().strftime("%Y-%m")


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {field}: expected YYYY-MM-DD")


def financial_year(d: date) -> str:
    """Indian FY April-March: 2026-04-01..2027-03-31 -> '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def _ym_of(value) -> Optional[str]:
    dt = _as_utc(value)
    return dt.astimezone(IST).strftime("%Y-%m") if dt else None


def _ts_expr(field: str = "created_at") -> dict:
    return {"$convert": {"input": f"${field}", "to": "date", "onError": None, "onNull": None}}


def _oid_or_none(value):
    return ObjectId(value) if isinstance(value, str) and ObjectId.is_valid(value) else None


async def _settings(db) -> dict:
    doc = await db.finance_settings.find_one({"_id": "default"}) or {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in doc.items() if k in DEFAULT_SETTINGS},
            "updated_at": doc.get("updated_at"), "updated_by": doc.get("updated_by")}


async def _bookings_between(db, start: datetime, end: datetime, extra: Optional[dict] = None,
                            fields: tuple = ("status", "amount", "vendor_id")) -> list[dict]:
    """Bookings whose created_at (string or date) falls in [start, end), with `_ym` (IST month)."""
    project = {f: 1 for f in fields}
    project["_ts"] = _ts_expr()
    pipe = []
    if extra:
        pipe.append({"$match": extra})
    pipe += [
        {"$project": project},
        {"$match": {"_ts": {"$gte": start, "$lt": end}}},
        {"$addFields": {"_ym": {"$dateToString": {"date": "$_ts", "format": "%Y-%m", "timezone": "Asia/Kolkata"}}}},
        {"$sort": {"_ts": 1, "_id": 1}},
    ]
    return await db.bookings.aggregate(pipe).to_list(None)


async def _live_invoice_booking_ids(db, booking_ids: Optional[list[str]] = None) -> set[str]:
    q: dict = {"active_booking_id": {"$exists": True}}
    if booking_ids is not None:
        q["active_booking_id"] = {"$in": booking_ids}
    return {d["active_booking_id"] async for d in db.invoices.find(q, {"active_booking_id": 1})}


async def _payout_items_for(db, booking_ids: Optional[list[str]] = None) -> dict[str, dict]:
    q = {} if booking_ids is None else {"booking_id": {"$in": booking_ids}}
    return {d["booking_id"]: d async for d in db.payout_items.find(q)}


async def _name_map(db, collection: str, ids) -> dict[str, str]:
    oids = [o for o in (_oid_or_none(i) for i in set(ids)) if o]
    if not oids:
        return {}
    return {str(d["_id"]): d.get("name") async for d in db[collection].find({"_id": {"$in": oids}}, {"name": 1})}


def _split(amount: float, pct: float) -> tuple[float, float]:
    commission = _money(Decimal(str(amount)) * Decimal(str(pct)) / 100)
    return commission, _money(Decimal(str(amount)) - Decimal(str(commission)))


def gst_breakup(amount: float, gst_pct: float, inclusive: bool, inter_state: bool) -> dict:
    """Taxable value, CGST/SGST (intra-state) or IGST (inter-state), total."""
    amt, rate = Decimal(str(amount or 0)), Decimal(str(gst_pct or 0))
    if inclusive:
        total = _money(amt)
        subtotal = _money(amt * 100 / (100 + rate))
        gst = _money(Decimal(str(total)) - Decimal(str(subtotal)))
    else:
        subtotal = _money(amt)
        gst = _money(amt * rate / 100)
        total = _money(Decimal(str(subtotal)) + Decimal(str(gst)))
    if inter_state:
        cgst = sgst = 0.0
        igst = gst
    else:
        cgst = _money(Decimal(str(gst)) / 2)
        sgst = _money(Decimal(str(gst)) - Decimal(str(cgst)))
        igst = 0.0
    return {"subtotal": subtotal, "cgst": cgst, "sgst": sgst, "igst": igst, "gst_total": gst, "total": total,
            "gst_type": "igst" if inter_state else "cgst_sgst"}


def _csv_cell(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return "" if v is None else v


def _csv_response(filename: str, header: list[str], rows: list[list]) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_csv_cell(c) for c in r])
    data = "﻿" + buf.getvalue()  # BOM so Excel reads ₹ / Indian names correctly
    return StreamingResponse(iter([data]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ------------------------------------------------------------------ settings

class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    commission_pct: float = Field(ge=0, le=100)
    gst_pct: float = Field(ge=0, le=100)
    invoice_prefix: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")
    prices_include_gst: bool = True
    company_name: str = Field(min_length=1, max_length=200)
    company_gstin: Optional[str] = Field(None, max_length=15)
    company_state: Optional[str] = Field(None, max_length=60)
    billing_address: Optional[str] = Field(None, max_length=500)

    @field_validator("company_gstin", "company_state", "billing_address", mode="before")
    @classmethod
    def _blank(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("company_gstin")
    @classmethod
    def _gstin(cls, v):
        if v is None:
            return v
        v = v.upper()
        if not GSTIN_RE.match(v):
            raise ValueError("GSTIN must be 15 characters (e.g. 10ABCDE1234F1Z5)")
        return v


@router.get("/settings")
async def get_settings(current: UserPublic = Viewer):
    return await _settings(get_db())


@router.put("/settings")
async def update_settings(payload: SettingsIn, current: UserPublic = Manager):
    db = get_db()
    before = await _settings(db)
    data = payload.model_dump()
    changed = {k: v for k, v in data.items() if before.get(k) != v}
    await db.finance_settings.update_one(
        {"_id": "default"},
        {"$set": {**data, "updated_at": utc_iso(), "updated_by": current.name}}, upsert=True)
    if changed:
        await log_activity(db, current, "Updated finance settings", MODULE,
                           target=", ".join(sorted(changed)), meta={"changes": changed})
    return await _settings(db)


# ------------------------------------------------------------------ invoices

class GenerateIn(BaseModel):
    booking_id: str
    invoice_date: Optional[str] = Field(None, pattern=DATE_RE)
    issue: bool = True
    notes: Optional[str] = Field(None, max_length=500)


class BulkGenerateIn(BaseModel):
    month: Optional[str] = Field(None, pattern=MONTH_RE)
    invoice_date: Optional[str] = Field(None, pattern=DATE_RE)
    issue: bool = True


class PayIn(BaseModel):
    method: Literal["upi", "card", "cash", "bank_transfer", "cheque", "other"]
    paid_on: Optional[str] = Field(None, pattern=DATE_RE)
    reference: Optional[str] = Field(None, max_length=100)


class VoidIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=300)


async def _allocate_number(db, settings: dict, invoice_date: str) -> tuple[str, str]:
    fy = financial_year(date.fromisoformat(invoice_date))
    counter = await db.finance_counters.find_one_and_update(
        {"_id": f"invoice:{fy}"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    return f"{settings['invoice_prefix']}/{fy}/{counter['seq']:04d}", fy


def _fmt_ist(value) -> str:
    dt = _as_utc(value)
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p") if dt else "-"


def _check_invoice_date(value: Optional[str]) -> None:
    """Invoices are never dated ahead: a future date would also consume the next FY's numbers."""
    if value and _parse_date(value, "invoice_date") > _today():
        raise HTTPException(400, "Invoice date cannot be in the future")


async def _create_invoice(db, booking: dict, settings: dict, current: UserPublic, invoice_date: Optional[str],
                          issue: bool, notes: Optional[str] = None) -> tuple[dict, bool]:
    """Returns (invoice, created). Idempotent: an existing live invoice for the booking is returned."""
    booking_id = str(booking["_id"])
    existing = await db.invoices.find_one({"active_booking_id": booking_id})
    if existing:
        return existing, False
    if booking.get("status") not in REVENUE_STATUSES:
        raise HTTPException(400, f"Booking is {booking.get('status')}; only confirmed, active or completed bookings can be invoiced")
    inv_date = invoice_date or _today().isoformat()
    _check_invoice_date(inv_date)

    customer = await db.customers.find_one({"_id": _oid_or_none(booking.get("customer_id"))}) if _oid_or_none(booking.get("customer_id")) else None
    vendor_name = None
    if _oid_or_none(booking.get("vendor_id")):
        v = await db.vendors.find_one({"_id": _oid_or_none(booking["vendor_id"])}, {"name": 1})
        vendor_name = v.get("name") if v else None
    city = booking.get("city")
    city_doc = await db.cities.find_one({"name": city}, {"state": 1}) if city else None
    place_state = (city_doc or {}).get("state")
    company_state = settings.get("company_state")
    inter_state = bool(company_state and place_state and company_state.strip().lower() != place_state.strip().lower())
    amount = _money(booking.get("amount"))
    b = gst_breakup(amount, settings["gst_pct"], settings["prices_include_gst"], inter_state)

    period = f"{_fmt_ist(booking.get('start_time'))} to {_fmt_ist(booking.get('end_time'))}"
    item = {"description": f"Two-wheeler rental - {booking.get('vehicle_label') or 'vehicle'}",
            "detail": f"{city or ''} - {period}".strip(" -"),
            "sac": "9966", "quantity": 1, "unit_price": b["subtotal"], "amount": b["subtotal"]}
    now = utc_iso()
    doc = {
        "booking_id": booking_id, "active_booking_id": booking_id,
        "number": None, "fy": None, "status": "draft", "invoice_date": inv_date,
        "customer_id": booking.get("customer_id"),
        "customer_name": (customer or {}).get("name") or booking.get("customer_name"),
        "customer_email": (customer or {}).get("email"), "customer_phone": (customer or {}).get("phone"),
        "customer_city": (customer or {}).get("city") or city,
        "vendor_id": booking.get("vendor_id"), "vendor_name": vendor_name,
        "city": city, "place_of_supply": place_state, "booking_status": booking.get("status"),
        "booking_created_at": booking.get("created_at"), "booking_amount": amount,
        "line_items": [item], "gst_pct": settings["gst_pct"], "prices_include_gst": settings["prices_include_gst"],
        **b,
        "company": {k: settings.get(k) for k in ("company_name", "company_gstin", "company_state", "billing_address")},
        "notes": notes, "payment": None, "paid_at": None, "void_reason": None, "voided_at": None,
        "created_by": current.name, "created_at": now, "updated_at": now,
    }
    try:
        res = await db.invoices.insert_one(doc)
    except DuplicateKeyError:  # raced with another request for the same booking
        return await db.invoices.find_one({"active_booking_id": booking_id}), False
    doc["_id"] = res.inserted_id
    if issue:
        doc = await _issue(db, doc, settings)
    return doc, True


async def _issue(db, inv: dict, settings: dict) -> dict:
    number, fy = await _allocate_number(db, settings, inv["invoice_date"])
    await db.invoices.update_one({"_id": inv["_id"], "status": "draft"},
                                 {"$set": {"number": number, "fy": fy, "status": "issued",
                                           "issued_at": utc_iso(), "updated_at": utc_iso()}})
    return await db.invoices.find_one({"_id": inv["_id"]})


def _invoice_query(status, month, customer_id, q) -> dict:
    query: dict = {}
    if status:
        if status not in ("draft", "issued", "paid", "void"):
            raise ValueError("Invalid status")
        query["status"] = status
    if month:
        if not re.match(MONTH_RE, month):
            raise ValueError("Invalid month: expected YYYY-MM")
        query["invoice_date"] = {"$regex": f"^{month}-"}
    if customer_id:
        query["customer_id"] = customer_id
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"number": rx}, {"customer_name": rx}, {"customer_email": rx}, {"booking_id": rx}]
    return query


@router.get("/invoices")
async def list_invoices(status: Optional[str] = None, month: Optional[str] = None, customer_id: Optional[str] = None,
                        q: Optional[str] = None, limit: int = Query(100, ge=1, le=500), skip: int = Query(0, ge=0),
                        current: UserPublic = Viewer):
    db = get_db()
    query = _invoice_query(status, month, customer_id, q)
    total = await db.invoices.count_documents(query)
    docs = await db.invoices.find(query).sort([("invoice_date", -1), ("created_at", -1)]).skip(skip).to_list(limit)
    agg = await db.invoices.aggregate([
        {"$match": query}, {"$group": {"_id": "$status", "n": {"$sum": 1}, "total": {"$sum": "$total"}}}]).to_list(None)
    summary = {s: {"count": 0, "total": 0.0} for s in ("draft", "issued", "paid", "void")}
    for a in agg:
        summary[a["_id"]] = {"count": a["n"], "total": _money(a["total"])}
    return {"items": [serialize(d) for d in docs], "total": total, "summary": summary}


@router.get("/invoices/export")
async def export_invoices(status: Optional[str] = None, month: Optional[str] = None, customer_id: Optional[str] = None,
                          q: Optional[str] = None, current: UserPublic = Viewer):
    db = get_db()
    docs = await db.invoices.find(_invoice_query(status, month, customer_id, q)).sort(
        [("invoice_date", 1), ("number", 1)]).to_list(None)
    header = ["Invoice number", "Invoice date", "Status", "Customer", "Customer email", "Booking id", "City",
              "Place of supply", "Taxable value", "GST %", "CGST", "SGST", "IGST", "GST total", "Total",
              "Paid on", "Payment method", "Payment reference", "Void reason"]
    rows = []
    for d in docs:
        p = d.get("payment") or {}
        rows.append([d.get("number") or "(draft)", d.get("invoice_date"), d.get("status"), d.get("customer_name"),
                     d.get("customer_email"), d.get("booking_id"), d.get("city"), d.get("place_of_supply"),
                     d.get("subtotal"), d.get("gst_pct"), d.get("cgst"), d.get("sgst"), d.get("igst"),
                     d.get("gst_total"), d.get("total"), p.get("paid_on"), p.get("method"), p.get("reference"),
                     d.get("void_reason")])
    await log_activity(db, current, "Exported invoices CSV", MODULE, target=month or "all", meta={"rows": len(rows)})
    return _csv_response(f"wavygo-invoices-{month or 'all'}.csv", header, rows)


@router.get("/invoices/eligible")
async def eligible_bookings(month: Optional[str] = Query(None, pattern=MONTH_RE), limit: int = Query(200, ge=1, le=1000),
                            current: UserPublic = Viewer):
    """Revenue bookings that do not have a live invoice yet."""
    db = get_db()
    q = {"status": {"$in": REVENUE_STATUSES}}
    if month:
        start, end = _month_range(month, month)
        docs = await _bookings_between(db, start, end, q, fields=(
            "status", "amount", "customer_name", "vehicle_label", "city", "created_at", "vendor_id"))
        docs.reverse()
    else:
        docs = await db.bookings.find(q, {"status": 1, "amount": 1, "customer_name": 1, "vehicle_label": 1,
                                          "city": 1, "created_at": 1, "vendor_id": 1}).to_list(None)
        docs.sort(key=lambda d: _as_utc(d.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    invoiced = await _live_invoice_booking_ids(db, [str(d["_id"]) for d in docs])
    rows = [d for d in docs if str(d["_id"]) not in invoiced]
    return {"total": len(rows), "amount": _money(sum(_money(d.get("amount")) for d in rows)),
            "items": [{"id": str(d["_id"]), "customer_name": d.get("customer_name"), "vehicle_label": d.get("vehicle_label"),
                       "city": d.get("city"), "status": d.get("status"), "amount": _money(d.get("amount")),
                       "created_at": d.get("created_at") if not isinstance(d.get("created_at"), datetime)
                       else _as_utc(d["created_at"]).isoformat()} for d in rows[:limit]]}


@router.post("/invoices", status_code=201)
async def generate_invoice(payload: GenerateIn, current: UserPublic = Manager):
    db = get_db()
    await _ready(db)
    _check_invoice_date(payload.invoice_date)
    booking = await db.bookings.find_one({"_id": oid(payload.booking_id)})
    if not booking:
        raise HTTPException(404, "Booking not found")
    settings = await _settings(db)
    inv, created = await _create_invoice(db, booking, settings, current, payload.invoice_date, payload.issue, payload.notes)
    if created:
        label = inv.get("number") or "Draft invoice"
        await log_activity(db, current, "Generated invoice", MODULE, target=f"{label} · {inv.get('customer_name')}",
                           meta={"invoice_id": str(inv["_id"]), "booking_id": inv["booking_id"], "total": inv["total"]})
        await notify(db, current.id, "Invoice generated",
                     f"{label} for {inv.get('customer_name')} · ₹{inv['total']:,.2f}", kind="success", link=LINK)
    return {**serialize(inv), "created": created}


@router.post("/invoices/bulk")
async def bulk_generate(payload: BulkGenerateIn, current: UserPublic = Manager):
    db = get_db()
    await _ready(db)
    _check_invoice_date(payload.invoice_date)
    settings = await _settings(db)
    q = {"status": {"$in": REVENUE_STATUSES}}
    if payload.month:
        start, end = _month_range(payload.month, payload.month)
    else:
        start, end = datetime(1970, 1, 1, tzinfo=timezone.utc), datetime(9999, 1, 1, tzinfo=timezone.utc)
    ids = [d["_id"] for d in await _bookings_between(db, start, end, q, fields=("status",))]
    invoiced = await _live_invoice_booking_ids(db, [str(i) for i in ids])
    todo = [i for i in ids if str(i) not in invoiced]
    created, total = 0, 0.0
    for bid in todo[:1000]:
        booking = await db.bookings.find_one({"_id": bid})
        if not booking or booking.get("status") not in REVENUE_STATUSES:
            continue
        inv, was_created = await _create_invoice(db, booking, settings, current, payload.invoice_date, payload.issue)
        if was_created:
            created += 1
            total += inv["total"]
    remaining = max(len(todo) - 1000, 0)
    if created:
        await log_activity(db, current, "Bulk generated invoices", MODULE, target=payload.month or "all months",
                           meta={"created": created, "total": _money(total)})
        await notify(db, current.id, "Invoices generated",
                     f"{created} invoice{'s' if created != 1 else ''} generated · ₹{_money(total):,.2f}",
                     kind="success", link=LINK)
    return {"created": created, "total": _money(total), "remaining": remaining}


async def _get_invoice(db, invoice_id: str) -> dict:
    inv = await db.invoices.find_one({"_id": oid(invoice_id)})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, current: UserPublic = Viewer):
    db = get_db()
    inv = await _get_invoice(db, invoice_id)
    booking = await db.bookings.find_one({"_id": _oid_or_none(inv.get("booking_id"))}, {"status": 1}) \
        if _oid_or_none(inv.get("booking_id")) else None
    return {**serialize(inv), "current_booking_status": (booking or {}).get("status")}


@router.post("/invoices/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, current: UserPublic = Manager):
    db = get_db()
    await _ready(db)
    inv = await _get_invoice(db, invoice_id)
    if inv["status"] != "draft":
        raise HTTPException(400, f"Invoice is already {inv['status']}")
    inv = await _issue(db, inv, await _settings(db))
    await log_activity(db, current, "Issued invoice", MODULE, target=f"{inv['number']} · {inv.get('customer_name')}")
    return serialize(inv)


@router.post("/invoices/{invoice_id}/pay")
async def mark_invoice_paid(invoice_id: str, payload: PayIn, current: UserPublic = Manager):
    db = get_db()
    inv = await _get_invoice(db, invoice_id)
    if inv["status"] != "issued":
        raise HTTPException(400, "Only issued invoices can be marked paid" if inv["status"] != "paid" else "Invoice is already paid")
    paid_on = payload.paid_on or _today().isoformat()
    if _parse_date(paid_on, "paid_on") > _today():
        raise HTTPException(400, "Payment date cannot be in the future")
    payment = {"paid_on": paid_on, "method": payload.method, "reference": (payload.reference or "").strip() or None,
               "recorded_by": current.name}
    res = await db.invoices.update_one({"_id": inv["_id"], "status": "issued"},
                                       {"$set": {"status": "paid", "payment": payment, "paid_at": utc_iso(), "updated_at": utc_iso()}})
    if not res.modified_count:
        raise HTTPException(409, "Invoice changed, reload and try again")
    inv = await db.invoices.find_one({"_id": inv["_id"]})
    await log_activity(db, current, "Marked invoice paid", MODULE, target=f"{inv['number']} · {inv.get('customer_name')}",
                       meta={"method": payload.method, "total": inv["total"]})
    await notify(db, current.id, "Invoice paid", f"{inv['number']} · ₹{inv['total']:,.2f} received via {payload.method.replace('_', ' ')}",
                 kind="success", link=LINK)
    return serialize(inv)


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: str, payload: VoidIn, current: UserPublic = Manager):
    db = get_db()
    inv = await _get_invoice(db, invoice_id)
    if inv["status"] == "void":
        raise HTTPException(400, "Invoice is already void")
    res = await db.invoices.update_one(
        {"_id": inv["_id"], "status": inv["status"]},
        {"$set": {"status": "void", "void_reason": payload.reason.strip(), "voided_at": utc_iso(),
                  "voided_from": inv["status"], "updated_at": utc_iso()},
         "$unset": {"active_booking_id": ""}})
    if not res.modified_count:
        raise HTTPException(409, "Invoice changed, reload and try again")
    inv = await db.invoices.find_one({"_id": inv["_id"]})
    label = inv.get("number") or "Draft invoice"
    await log_activity(db, current, "Voided invoice", MODULE, target=f"{label} · {inv.get('customer_name')}",
                       meta={"reason": inv["void_reason"], "was": inv["voided_from"]})
    await notify(db, current.id, "Invoice voided", f"{label}: {inv['void_reason']}", kind="warning", link=LINK)
    return serialize(inv)


# ------------------------------------------------------------------ payouts

class PayoutCreateIn(BaseModel):
    period_start: str = Field(pattern=DATE_RE)
    period_end: str = Field(pattern=DATE_RE)
    vendor_ids: Optional[list[str]] = None
    notes: Optional[str] = Field(None, max_length=300)


class PayoutPayIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reference: str = Field(min_length=1, max_length=100)
    paid_on: Optional[str] = Field(None, pattern=DATE_RE)


@router.get("/payouts/outstanding")
async def payouts_outstanding(current: UserPublic = Viewer):
    """Per vendor: earnings not yet in any payout (all revenue bookings), the payable part
    (completed bookings), and pending payout batches awaiting transfer."""
    db = get_db()
    settings = await _settings(db)
    pct = settings["commission_pct"]
    docs = await db.bookings.find({"status": {"$in": REVENUE_STATUSES}},
                                  {"status": 1, "amount": 1, "vendor_id": 1}).to_list(None)
    in_payout = await _payout_items_for(db, [str(d["_id"]) for d in docs])
    rows: dict[str, dict] = {}
    unassigned = {"bookings": 0, "gross": 0.0}

    def row(vid):
        return rows.setdefault(vid, {"vendor_id": vid, "earned_bookings": 0, "earned_gross": 0.0, "earned_net": 0.0,
                                     "payable_bookings": 0, "payable_gross": 0.0, "payable_commission": 0.0,
                                     "payable_net": 0.0, "pending_payouts": 0, "pending_net": 0.0, "paid_net": 0.0})
    for d in docs:
        if str(d["_id"]) in in_payout:
            continue
        amount = _money(d.get("amount"))
        if not d.get("vendor_id"):
            unassigned["bookings"] += 1
            unassigned["gross"] += amount
            continue
        commission, net = _split(amount, pct)
        r = row(d["vendor_id"])
        r["earned_bookings"] += 1
        r["earned_gross"] += amount
        r["earned_net"] += net
        if d.get("status") == PAYABLE_STATUS:
            r["payable_bookings"] += 1
            r["payable_gross"] += amount
            r["payable_commission"] += commission
            r["payable_net"] += net
    async for p in db.payouts.find({}, {"vendor_id": 1, "status": 1, "net": 1}):
        r = row(p["vendor_id"])
        if p["status"] == "pending":
            r["pending_payouts"] += 1
            r["pending_net"] += p["net"]
        else:
            r["paid_net"] += p["net"]
    names = await _name_map(db, "vendors", rows.keys())
    out = []
    for vid, r in rows.items():
        out.append({**{k: (_money(v) if isinstance(v, float) else v) for k, v in r.items()},
                    "vendor_name": names.get(vid) or "Unknown vendor"})
    out.sort(key=lambda r: (-(r["earned_net"] + r["pending_net"]), r["vendor_name"]))
    totals = {k: _money(sum(r[k] for r in out)) for k in
              ("earned_gross", "earned_net", "payable_gross", "payable_commission", "payable_net", "pending_net", "paid_net")}
    totals["payable_bookings"] = sum(r["payable_bookings"] for r in out)
    return {"commission_pct": pct, "vendors": out, "totals": totals,
            "unassigned": {"bookings": unassigned["bookings"], "gross": _money(unassigned["gross"])}}


@router.get("/payouts")
async def list_payouts(status: Optional[Literal["pending", "paid"]] = None, vendor_id: Optional[str] = None,
                       batch_id: Optional[str] = None, limit: int = Query(200, ge=1, le=500),
                       current: UserPublic = Viewer):
    db = get_db()
    q: dict = {}
    if status:
        q["status"] = status
    if vendor_id:
        q["vendor_id"] = vendor_id
    if batch_id:
        q["batch_id"] = batch_id
    docs = await db.payouts.find(q).sort([("created_at", -1), ("vendor_name", 1)]).to_list(limit)
    return [serialize(d) for d in docs]


@router.post("/payouts", status_code=201)
async def create_payout_batch(payload: PayoutCreateIn, current: UserPublic = Manager):
    db = get_db()
    await _ready(db)
    start_d = _parse_date(payload.period_start, "period_start")
    end_d = _parse_date(payload.period_end, "period_end")
    if end_d < start_d:
        raise HTTPException(400, "Period end must be on or after period start")
    if start_d > _today():
        raise HTTPException(400, "Period cannot start in the future")
    settings = await _settings(db)
    pct = settings["commission_pct"]
    q: dict = {"status": PAYABLE_STATUS, "vendor_id": {"$nin": [None, ""]}}
    if payload.vendor_ids:
        q["vendor_id"] = {"$in": payload.vendor_ids}
    docs = await _bookings_between(db, _ist_midnight(start_d), _ist_midnight(end_d + timedelta(days=1)), q,
                                   fields=("status", "amount", "vendor_id", "customer_name"))
    taken = await _payout_items_for(db, [str(d["_id"]) for d in docs])
    by_vendor: dict[str, list[dict]] = {}
    for d in docs:
        if str(d["_id"]) not in taken:
            by_vendor.setdefault(d["vendor_id"], []).append(d)
    if not by_vendor:
        raise HTTPException(400, "No completed bookings awaiting payout in this period")
    names = await _name_map(db, "vendors", by_vendor.keys())
    batch_id, now = uuid.uuid4().hex[:12], utc_iso()
    created = []
    for vid, bookings in by_vendor.items():
        payout_id = ObjectId()
        items = []
        for b in bookings:
            amount = _money(b.get("amount"))
            commission, net = _split(amount, pct)
            items.append({"booking_id": str(b["_id"]), "payout_id": payout_id, "vendor_id": vid, "amount": amount,
                          "commission_pct": pct, "commission": commission, "net": net, "created_at": now})
        try:
            await db.payout_items.insert_many(items, ordered=False)
            kept = items
        except BulkWriteError as e:  # concurrent batch grabbed some bookings first
            failed = {err["index"] for err in e.details.get("writeErrors", [])}
            kept = [it for i, it in enumerate(items) if i not in failed]
        if not kept:
            continue
        doc = {
            "_id": payout_id, "batch_id": batch_id, "vendor_id": vid, "vendor_name": names.get(vid) or "Unknown vendor",
            "period_start": payload.period_start, "period_end": payload.period_end,
            "booking_ids": [it["booking_id"] for it in kept], "bookings_count": len(kept),
            "gross": _money(sum(it["amount"] for it in kept)), "commission_pct": pct,
            "commission": _money(sum(it["commission"] for it in kept)), "net": _money(sum(it["net"] for it in kept)),
            "status": "pending", "reference": None, "paid_on": None, "paid_at": None, "notes": payload.notes,
            "created_by": current.name, "created_at": now, "updated_at": now,
        }
        await db.payouts.insert_one(doc)
        created.append(doc)
    if not created:
        raise HTTPException(409, "These bookings were just added to another payout")
    totals = {k: _money(sum(p[k] for p in created)) for k in ("gross", "commission", "net")}
    await log_activity(db, current, "Created payout batch", MODULE,
                       target=f"{payload.period_start} to {payload.period_end} · {len(created)} vendor(s)",
                       meta={"batch_id": batch_id, **totals})
    await notify(db, current.id, "Vendor payout batch created",
                 f"{len(created)} vendor payout(s) · ₹{totals['net']:,.2f} to transfer", kind="info", link=LINK)
    return {"batch_id": batch_id, "payouts": [serialize(p) for p in created], "totals": totals}


@router.post("/payouts/{payout_id}/pay")
async def mark_payout_paid(payout_id: str, payload: PayoutPayIn, current: UserPublic = Manager):
    db = get_db()
    p = await db.payouts.find_one({"_id": oid(payout_id)})
    if not p:
        raise HTTPException(404, "Payout not found")
    if p["status"] != "pending":
        raise HTTPException(400, "Payout is already paid")
    paid_on = payload.paid_on or _today().isoformat()
    if _parse_date(paid_on, "paid_on") > _today():
        raise HTTPException(400, "Payment date cannot be in the future")
    res = await db.payouts.update_one({"_id": p["_id"], "status": "pending"}, {"$set": {
        "status": "paid", "reference": payload.reference.strip(), "paid_on": paid_on, "paid_at": utc_iso(),
        "paid_by": current.name, "updated_at": utc_iso()}})
    if not res.modified_count:
        raise HTTPException(409, "Payout changed, reload and try again")
    p = await db.payouts.find_one({"_id": p["_id"]})
    await log_activity(db, current, "Marked vendor payout paid", MODULE, target=f"{p['vendor_name']} · ₹{p['net']:,.2f}",
                       meta={"reference": p["reference"], "batch_id": p["batch_id"]})
    await notify(db, current.id, "Vendor payout paid", f"{p['vendor_name']} · ₹{p['net']:,.2f} (ref {p['reference']})",
                 kind="success", link=LINK)
    return serialize(p)


@router.delete("/payouts/{payout_id}")
async def cancel_payout(payout_id: str, current: UserPublic = Manager):
    """Cancel a pending payout; its bookings become available for a future batch."""
    db = get_db()
    p = await db.payouts.find_one({"_id": oid(payout_id)})
    if not p:
        raise HTTPException(404, "Payout not found")
    res = await db.payouts.delete_one({"_id": p["_id"], "status": "pending"})
    if not res.deleted_count:
        raise HTTPException(400, "Only pending payouts can be cancelled")
    await db.payout_items.delete_many({"payout_id": p["_id"]})
    await log_activity(db, current, "Cancelled vendor payout", MODULE, target=f"{p['vendor_name']} · ₹{p['net']:,.2f}",
                       meta={"batch_id": p["batch_id"]})
    return {"ok": True}


# ------------------------------------------------------------------ statements

STAT_FIELDS = ("gross_revenue", "revenue_bookings", "platform_commission", "vendor_earnings", "vendor_paid",
               "vendor_owed", "gst_collected", "invoices_issued", "invoiced_amount", "invoices_paid", "paid_amount",
               "invoices_outstanding", "outstanding_amount", "invoices_void", "refunded_amount",
               "cancelled_bookings", "pending_bookings", "uninvoiced_bookings")


async def month_stats(db, first: str, last: str, settings: dict) -> dict[str, dict]:
    """Per-month (IST) figures for [first, last]. Bookings are dated by created_at, invoices by
    invoice_date, voids by voided_at. Commission uses the rate frozen in a payout when the booking
    has been paid out, otherwise the current setting."""
    months, m = [], first
    while m <= last:
        months.append(m)
        m = _add_months(m, 1)
    stats = {ym: {f: 0 for f in STAT_FIELDS} for ym in months}
    start, end = _month_range(first, last)
    pct = settings["commission_pct"]

    bookings = await _bookings_between(db, start, end)
    rev_ids = [str(b["_id"]) for b in bookings if b.get("status") in REVENUE_STATUSES]
    items = await _payout_items_for(db, rev_ids)
    payout_status = {p["_id"]: p["status"] async for p in db.payouts.find(
        {"_id": {"$in": list({i["payout_id"] for i in items.values()})}}, {"status": 1})}
    invoiced = await _live_invoice_booking_ids(db, rev_ids)
    for b in bookings:
        s = stats[b["_ym"]]
        status = b.get("status")
        if status == "cancelled":
            s["cancelled_bookings"] += 1
        elif status == "pending":
            s["pending_bookings"] += 1
        if status not in REVENUE_STATUSES:
            continue
        bid, amount = str(b["_id"]), _money(b.get("amount"))
        s["revenue_bookings"] += 1
        s["gross_revenue"] += amount
        item = items.get(bid)
        if item:
            commission, net = item["commission"], item["net"]
            if payout_status.get(item["payout_id"]) == "paid":
                s["vendor_paid"] += net
            else:
                s["vendor_owed"] += net
        else:
            commission, net = _split(amount, pct) if b.get("vendor_id") else (amount, 0.0)
            s["vendor_owed"] += net
        s["platform_commission"] += commission
        s["vendor_earnings"] += net
        if bid not in invoiced:
            s["uninvoiced_bookings"] += 1

    inv_q = {"$or": [
        {"invoice_date": {"$gte": f"{first}-01", "$lt": f"{_add_months(last, 1)}-01"}},
        {"voided_at": {"$ne": None}},
    ]}
    async for inv in db.invoices.find(inv_q, {"status": 1, "invoice_date": 1, "total": 1, "gst_total": 1,
                                              "voided_at": 1, "voided_from": 1, "payment": 1}):
        ym = (inv.get("invoice_date") or "")[:7]
        status = inv.get("status")
        if ym in stats:
            s = stats[ym]
            if status in ("issued", "paid"):
                s["gst_collected"] += inv.get("gst_total") or 0
                s["invoices_issued"] += 1
                s["invoiced_amount"] += inv.get("total") or 0
            if status == "paid":
                s["invoices_paid"] += 1
                s["paid_amount"] += inv.get("total") or 0
            elif status == "issued":
                s["invoices_outstanding"] += 1
                s["outstanding_amount"] += inv.get("total") or 0
        if status == "void":
            vym = _ym_of(inv.get("voided_at"))
            if vym in stats:
                stats[vym]["invoices_void"] += 1
                if inv.get("voided_from") == "paid":
                    stats[vym]["refunded_amount"] += inv.get("total") or 0
    for s in stats.values():
        for k, v in s.items():
            if isinstance(v, float):
                s[k] = _money(v)
    return stats


def _check_month(month: Optional[str]) -> str:
    month = month or _current_month()
    if not re.match(MONTH_RE, month):
        raise ValueError("Invalid month: expected YYYY-MM")
    return month


async def _statement(db, month: str) -> dict:
    settings = await _settings(db)
    first = _add_months(month, -11)
    stats = await month_stats(db, first, month, settings)
    trailing = [{"month": ym, "label": _month_label(ym), **stats[ym]} for ym in sorted(stats)]
    totals = {f: (_money(sum(t[f] for t in trailing)) if isinstance(trailing[0][f], float) else sum(t[f] for t in trailing))
              for f in STAT_FIELDS}
    return {"month": month, "label": _month_label(month), "summary": stats[month], "trailing": trailing,
            "trailing_totals": totals, "commission_pct": settings["commission_pct"], "gst_pct": settings["gst_pct"]}


@router.get("/statements")
async def get_statement(month: Optional[str] = None, current: UserPublic = Viewer):
    return await _statement(get_db(), _check_month(month))


STAT_LABELS = {
    "gross_revenue": "Gross booking revenue", "revenue_bookings": "Revenue bookings",
    "platform_commission": "Platform commission", "vendor_earnings": "Vendor earnings",
    "vendor_paid": "Vendor payouts paid", "vendor_owed": "Vendor payouts owed", "gst_collected": "GST collected",
    "invoices_issued": "Invoices issued", "invoiced_amount": "Invoiced amount", "invoices_paid": "Invoices paid",
    "paid_amount": "Paid amount", "invoices_outstanding": "Invoices outstanding", "outstanding_amount": "Outstanding amount",
    "invoices_void": "Invoices voided", "refunded_amount": "Refunded (voided paid invoices)",
    "cancelled_bookings": "Cancelled bookings", "pending_bookings": "Pending bookings",
    "uninvoiced_bookings": "Uninvoiced revenue bookings",
}


@router.get("/statements/export")
async def export_statement(month: Optional[str] = None, current: UserPublic = Viewer):
    db = get_db()
    st = await _statement(db, _check_month(month))
    header = ["Month"] + [STAT_LABELS[f] for f in STAT_FIELDS]
    rows = [[t["month"]] + [t[f] for f in STAT_FIELDS] for t in st["trailing"]]
    rows.append(["Trailing 12 months"] + [st["trailing_totals"][f] for f in STAT_FIELDS])
    await log_activity(db, current, "Exported finance statement", MODULE, target=st["month"])
    return _csv_response(f"wavygo-statement-{st['month']}.csv", header, rows)


# ------------------------------------------------------------------ overview

@router.get("/overview")
async def overview(current: UserPublic = Viewer):
    db = get_db()
    settings = await _settings(db)
    month = _current_month()
    stats = await month_stats(db, _add_months(month, -11), month, settings)
    cur, prev = stats[month], stats[_add_months(month, -1)]
    receivable = await db.invoices.aggregate([
        {"$match": {"status": "issued"}}, {"$group": {"_id": None, "n": {"$sum": 1}, "total": {"$sum": "$total"}}}]).to_list(1)
    drafts = await db.invoices.count_documents({"status": "draft"})
    pending_payouts = await db.payouts.aggregate([
        {"$match": {"status": "pending"}}, {"$group": {"_id": None, "n": {"$sum": 1}, "net": {"$sum": "$net"}}}]).to_list(1)
    rev = await db.bookings.find({"status": {"$in": REVENUE_STATUSES}}, {"_id": 1}).to_list(None)
    rev_ids = [str(b["_id"]) for b in rev]
    uninvoiced = len(rev_ids) - len(await _live_invoice_booking_ids(db, rev_ids))
    return {
        "month": month, "label": _month_label(month),
        "commission_pct": settings["commission_pct"], "gst_pct": settings["gst_pct"],
        "cards": {
            "gross_mtd": cur["gross_revenue"], "gross_prev": prev["gross_revenue"],
            "commission_mtd": cur["platform_commission"], "commission_prev": prev["platform_commission"],
            "gst_mtd": cur["gst_collected"], "bookings_mtd": cur["revenue_bookings"],
            "receivable": _money(receivable[0]["total"]) if receivable else 0.0,
            "receivable_count": receivable[0]["n"] if receivable else 0,
            "draft_invoices": drafts, "uninvoiced_bookings": uninvoiced,
            "payouts_pending": _money(pending_payouts[0]["net"]) if pending_payouts else 0.0,
            "payouts_pending_count": pending_payouts[0]["n"] if pending_payouts else 0,
        },
        "series": [{"month": ym, "label": _month_start(ym).strftime("%b"), "revenue": stats[ym]["gross_revenue"],
                    "commission": stats[ym]["platform_commission"], "gst": stats[ym]["gst_collected"]}
                   for ym in sorted(stats)],
    }

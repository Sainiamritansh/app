"""Finance module tests against an isolated backend + throwaway DB (see local_harness.py).

Run: .venv/Scripts/python -m pytest tests/finance_local_test.py -q -n 0

The tests in this module build on each other (numbering sequence, payouts) and run in file order.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from local_harness import api, mongo, test_db, users, harness_env, call  # noqa: F401

B = {}  # booking key -> id


@pytest.fixture(scope="module", autouse=True)
def seed(test_db, users):
    for c in ("bookings", "vendors", "customers", "cities", "invoices", "payouts", "payout_items",
              "finance_settings", "finance_counters"):
        test_db[c].delete_many({})
    test_db.cities.insert_one({"name": "Patna", "state": "Bihar", "status": "active"})
    va = str(test_db.vendors.insert_one({"name": "Vendor A", "city": "Patna"}).inserted_id)
    vb = str(test_db.vendors.insert_one({"name": "Vendor B", "city": "Patna"}).inserted_id)
    cu = str(test_db.customers.insert_one({"name": "Asha Kumari", "email": "asha@example.in", "city": "Patna"}).inserted_id)
    rows = {
        "b1": ("completed", 1180, va, "2026-07-05T06:00:00+00:00"),
        "b2": ("completed", 590, va, "2026-07-06T06:00:00.123456+00:00"),
        "b3": ("active", 1000, vb, "2026-07-07T06:00:00Z"),
        "b4": ("confirmed", 2000, vb, datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)),  # BSON date
        "b5": ("pending", 999, va, "2026-07-09T06:00:00+00:00"),
        "b6": ("cancelled", 777, vb, "2026-07-10T06:00:00+00:00"),
        # 31 Jul 20:00 UTC = 1 Aug 01:30 IST -> belongs to August
        "b7": ("completed", 300, vb, "2026-07-31T20:00:00+00:00"),
    }
    for key, (status, amount, vendor, created) in rows.items():
        B[key] = str(test_db.bookings.insert_one({
            "customer_id": cu, "customer_name": "Asha Kumari", "vehicle_id": str(ObjectId()),
            "vehicle_label": "Ather 450X · BRPA1001", "vendor_id": vendor, "city": "Patna",
            "start_time": "2026-07-05T06:00:00+00:00", "end_time": "2026-07-06T06:00:00+00:00",
            "amount": amount, "status": status, "created_at": created, "updated_at": created,
        }).inserted_id)
    B["vendor_a"], B["vendor_b"] = va, vb


def F(api, users, method, path, who="founder", **kw):
    return call(api, users[who], method, f"/finance{path}", **kw)


def gen(api, users, key, date=None, **extra):
    body = {"booking_id": B[key], **({"invoice_date": date} if date else {}), **extra}
    return F(api, users, "POST", "/invoices", json=body)


# ------------------------------------------------------------------ RBAC

@pytest.mark.parametrize("who", ["admin", "manager", "employee", "intern"])
def test_non_founders_are_forbidden(api, users, who):
    for method, path, kw in [("GET", "/overview", {}), ("GET", "/invoices", {}), ("GET", "/settings", {}),
                             ("GET", "/statements/export", {}), ("GET", "/payouts/outstanding", {}),
                             ("POST", "/invoices/bulk", {"json": {}}),
                             ("POST", "/payouts", {"json": {"period_start": "2026-07-01", "period_end": "2026-07-31"}})]:
        r = F(api, users, method, path, who=who, **kw)
        assert r.status_code == 403, (who, path, r.text)


def test_unauthenticated(api):
    assert call(api, None, "GET", "/finance/overview").status_code == 401


# ------------------------------------------------------------------ settings

def test_settings_defaults_and_validation(api, users):
    s = F(api, users, "GET", "/settings").json()
    assert (s["commission_pct"], s["gst_pct"], s["invoice_prefix"], s["prices_include_gst"]) == (20, 18, "WG-INV", True)
    bad = {**s, "company_gstin": "NOT-A-GSTIN"}
    assert F(api, users, "PUT", "/settings", json=bad).status_code == 422
    assert F(api, users, "PUT", "/settings", json={**s, "commission_pct": 120}).status_code == 422


# ------------------------------------------------------------------ invoices

def test_invoice_rules_numbering_gst_idempotency(api, users, test_db):
    assert gen(api, users, "b5").status_code == 400          # pending never invoiced
    assert gen(api, users, "b6").status_code == 400          # cancelled never invoiced

    r = gen(api, users, "b1", "2026-07-10")
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["created"] is True and inv["status"] == "issued"
    assert inv["number"] == "WG-INV/2026-27/0001"
    # 1180 incl. 18% GST, intra-state -> 1000 + 90 CGST + 90 SGST
    assert (inv["subtotal"], inv["cgst"], inv["sgst"], inv["igst"], inv["total"]) == (1000, 90, 90, 0, 1180)
    assert inv["line_items"][0]["amount"] == 1000

    again = gen(api, users, "b1", "2026-07-12").json()
    assert again["created"] is False and again["id"] == inv["id"] and again["number"] == inv["number"]
    assert test_db.invoices.count_documents({"booking_id": B["b1"]}) == 1

    # FY runs April-March
    assert gen(api, users, "b2", "2026-04-01").json()["number"] == "WG-INV/2026-27/0002"
    assert gen(api, users, "b3", "2026-03-31").json()["number"] == "WG-INV/2025-26/0001"


def test_invoice_date_cannot_be_in_the_future(api, users, test_db):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    r = gen(api, users, "b4", tomorrow, issue=False)
    assert r.status_code == 400 and "future" in r.json()["detail"]
    assert F(api, users, "POST", "/invoices/bulk", json={"invoice_date": tomorrow}).status_code == 400
    assert test_db.invoices.count_documents({"booking_id": B["b4"]}) == 0


def test_void_and_reissue(api, users):
    inv = gen(api, users, "b3").json()
    assert inv["created"] is False
    assert F(api, users, "POST", f"/invoices/{inv['id']}/void", json={"reason": "x"}).status_code == 422
    assert F(api, users, "POST", f"/invoices/{inv['id']}/void", json={"reason": "  x   "}).status_code == 422
    v = F(api, users, "POST", f"/invoices/{inv['id']}/void", json={"reason": "Wrong financial year"})
    assert v.status_code == 200 and v.json()["status"] == "void"
    assert F(api, users, "POST", f"/invoices/{inv['id']}/void", json={"reason": "again please"}).status_code == 400
    assert F(api, users, "POST", f"/invoices/{inv['id']}/pay", json={"method": "upi"}).status_code == 400
    new = gen(api, users, "b3", "2026-07-20").json()
    assert new["created"] is True and new["id"] != inv["id"]
    assert new["number"] == "WG-INV/2026-27/0003"
    assert (new["subtotal"], new["cgst"], new["sgst"], new["total"]) == (847.46, 76.27, 76.27, 1000)


def test_mark_paid(api, users):
    inv = gen(api, users, "b1").json()
    assert F(api, users, "POST", f"/invoices/{inv['id']}/pay", json={"method": "bitcoin"}).status_code == 422
    assert F(api, users, "POST", f"/invoices/{inv['id']}/pay", json={"method": "upi", "paid_on": "2099-01-01"}).status_code == 400
    r = F(api, users, "POST", f"/invoices/{inv['id']}/pay", json={"method": "upi", "reference": "UTR123", "paid_on": "2026-07-11"})
    assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["payment"]["reference"] == "UTR123"
    assert F(api, users, "POST", f"/invoices/{inv['id']}/pay", json={"method": "upi"}).status_code == 400


def test_exclusive_gst_and_inter_state(api, users):
    s = F(api, users, "GET", "/settings").json()
    r = F(api, users, "PUT", "/settings", json={**s, "prices_include_gst": False, "company_state": "Karnataka"})
    assert r.status_code == 200
    inv = gen(api, users, "b4", "2026-07-21").json()
    assert inv["number"] == "WG-INV/2026-27/0004"
    assert (inv["subtotal"], inv["igst"], inv["cgst"], inv["gst_total"], inv["total"], inv["gst_type"]) == \
        (2000, 360, 0, 360, 2360, "igst")
    assert F(api, users, "PUT", "/settings", json=s).status_code == 200


def test_draft_then_issue_and_bulk(api, users):
    elig = F(api, users, "GET", "/invoices/eligible").json()
    assert [i["id"] for i in elig["items"]] == [B["b7"]]
    r = F(api, users, "POST", "/invoices/bulk", json={"issue": False, "invoice_date": "2026-08-02"}).json()
    assert r["created"] == 1
    assert F(api, users, "POST", "/invoices/bulk", json={}).json()["created"] == 0
    draft = F(api, users, "GET", "/invoices", params={"status": "draft"}).json()["items"]
    assert len(draft) == 1 and draft[0]["number"] is None
    issued = F(api, users, "POST", f"/invoices/{draft[0]['id']}/issue").json()
    assert issued["number"] == "WG-INV/2026-27/0005" and issued["status"] == "issued"

    lst = F(api, users, "GET", "/invoices", params={"month": "2026-07"}).json()
    assert lst["total"] == 3  # b1 paid, b3 reissued, b4 (voided b3 invoice is dated 2026-03-31)
    assert lst["summary"]["paid"]["count"] == 1 and lst["summary"]["issued"]["count"] == 2
    assert F(api, users, "GET", "/invoices", params={"q": "2025-26"}).json()["total"] == 1


# ------------------------------------------------------------------ statements (before payouts)

def test_statement_totals(api, users):
    st = F(api, users, "GET", "/statements", params={"month": "2026-07"}).json()
    s = st["summary"]
    assert s["gross_revenue"] == 4770            # 1180 + 590 + 1000 + 2000; pending / cancelled excluded
    assert s["revenue_bookings"] == 4 and s["cancelled_bookings"] == 1 and s["pending_bookings"] == 1
    assert s["platform_commission"] == 954        # 20%
    assert s["vendor_earnings"] == 3816 and s["vendor_owed"] == 3816 and s["vendor_paid"] == 0
    assert s["gst_collected"] == 692.54           # 180 + 152.54 + 360 (void excluded)
    assert (s["invoices_issued"], s["invoices_paid"], s["paid_amount"]) == (3, 1, 1180)
    assert (s["invoices_outstanding"], s["outstanding_amount"]) == (2, 3360)
    assert s["uninvoiced_bookings"] == 0
    assert len(st["trailing"]) == 12 and st["trailing"][-1]["month"] == "2026-07"
    aug = F(api, users, "GET", "/statements", params={"month": "2026-08"}).json()["summary"]
    assert aug["gross_revenue"] == 300            # IST day boundary
    assert F(api, users, "GET", "/statements", params={"month": "2026-13"}).status_code == 400


# ------------------------------------------------------------------ payouts

def test_payout_batching(api, users, test_db):
    body = {"period_start": "2026-07-01", "period_end": "2026-07-31"}
    r = F(api, users, "POST", "/payouts", json=body)
    assert r.status_code == 201, r.text
    batch = r.json()
    assert len(batch["payouts"]) == 1                 # only vendor A has completed July bookings
    p = batch["payouts"][0]
    assert p["vendor_id"] == B["vendor_a"] and sorted(p["booking_ids"]) == sorted([B["b1"], B["b2"]])
    assert (p["gross"], p["commission"], p["net"], p["status"]) == (1770, 354, 1416, "pending")
    assert F(api, users, "POST", "/payouts", json=body).status_code == 400   # no double inclusion

    wide = F(api, users, "POST", "/payouts", json={"period_start": "2026-07-01", "period_end": "2026-08-31"}).json()
    assert [(x["vendor_id"], x["net"]) for x in wide["payouts"]] == [(B["vendor_b"], 240)]
    assert test_db.payout_items.count_documents({}) == 3

    out = {v["vendor_id"]: v for v in F(api, users, "GET", "/payouts/outstanding").json()["vendors"]}
    assert out[B["vendor_b"]]["earned_gross"] == 3000 and out[B["vendor_b"]]["payable_net"] == 0
    assert out[B["vendor_b"]]["pending_net"] == 240 and out[B["vendor_a"]]["pending_net"] == 1416

    # Commission change affects only bookings not yet in a payout
    s = F(api, users, "GET", "/settings").json()
    F(api, users, "PUT", "/settings", json={**s, "commission_pct": 10})
    assert F(api, users, "POST", f"/payouts/{p['id']}/pay", json={"reference": "   "}).status_code == 422
    paid = F(api, users, "POST", f"/payouts/{p['id']}/pay", json={"reference": " NEFT-001 ", "paid_on": "2026-08-05"})
    assert paid.status_code == 200 and paid.json()["status"] == "paid" and paid.json()["reference"] == "NEFT-001"
    assert F(api, users, "POST", f"/payouts/{p['id']}/pay", json={"reference": "again"}).status_code == 400
    st = F(api, users, "GET", "/statements", params={"month": "2026-07"}).json()["summary"]
    assert st["platform_commission"] == 654       # 354 frozen + 10% of 3000
    assert st["vendor_paid"] == 1416 and st["vendor_owed"] == 2700

    assert F(api, users, "DELETE", f"/payouts/{p['id']}").status_code == 400          # paid cannot be cancelled
    b_id = wide["payouts"][0]["id"]
    assert F(api, users, "DELETE", f"/payouts/{b_id}").status_code == 200
    out = {v["vendor_id"]: v for v in F(api, users, "GET", "/payouts/outstanding").json()["vendors"]}
    assert out[B["vendor_b"]]["payable_net"] == 270 and out[B["vendor_b"]]["pending_net"] == 0
    F(api, users, "PUT", "/settings", json=s)


# ------------------------------------------------------------------ exports, overview, audit

def test_csv_exports(api, users):
    r = F(api, users, "GET", "/statements/export", params={"month": "2026-07"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][0] == "Month" and len(rows) == 14
    jul = next(x for x in rows if x[0] == "2026-07")
    assert float(jul[1]) == 4770
    r = F(api, users, "GET", "/invoices/export")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 1 + 6
    assert "WG-INV/2026-27/0001" in {x[0] for x in rows}


def test_overview_and_audit(api, users, test_db):
    o = F(api, users, "GET", "/overview").json()
    assert len(o["series"]) == 12 and o["cards"]["uninvoiced_bookings"] == 0
    assert o["cards"]["receivable_count"] == 4     # b2, b3 reissue, b4, b7
    actions = {a["action"] for a in test_db.activity_logs.find({"module": "Finance"})}
    assert {"Generated invoice", "Voided invoice", "Marked invoice paid", "Created payout batch",
            "Marked vendor payout paid", "Updated finance settings", "Exported finance statement"} <= actions
    assert test_db.notifications.count_documents({"link": "/finance"}) >= 4

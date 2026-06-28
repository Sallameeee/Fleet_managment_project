"""Platform finance summary (super-admin only).

Per org: monthly_fee, status, expiry, plus expected / collected / outstanding.
Plus platform-wide totals.

How the numbers are computed from the schema:
  * expected  = the org's monthly_fee (what it owes per cycle).
  * collected = sum of that org's `payments` rows with status = 'paid'.
  * outstanding = expected - collected.
Orgs with no payments simply get collected = 0 (outstanding = expected).

NOTE on interpretation: expected is ONE month's fee while collected is the sum of
ALL paid payments, so an org that has paid multiple cycles can show negative
outstanding (credit). That's the literal spec; if you want a period-scoped view
(e.g. this month's expected vs this month's payments) it's a small change.
"""

from collections import defaultdict

from fastapi import APIRouter, Depends

from auth import require_super_admin
from database import supabase

router = APIRouter(prefix="/finance", tags=["finance"])


@router.get("")
def finance_summary(_admin: dict = Depends(require_super_admin)):
    orgs = (
        supabase.table("organizations")
        .select("id, name, status, monthly_fee, subscription_expiry")
        .order("name", desc=False)
        .execute()
    ).data

    # One query for all paid payments; sum per org in memory (no N+1).
    paid = (
        supabase.table("payments").select("org_id, amount").eq("status", "paid").execute()
    ).data
    collected_by_org: dict = defaultdict(float)
    for p in paid:
        collected_by_org[p["org_id"]] += float(p["amount"] or 0)

    rows = []
    total_expected = 0.0
    total_collected = 0.0
    for o in orgs:
        expected = float(o["monthly_fee"] or 0)
        collected = round(collected_by_org.get(o["id"], 0.0), 2)
        total_expected += expected
        total_collected += collected
        rows.append(
            {
                "id": o["id"],
                "name": o["name"],
                "status": o["status"],
                "monthly_fee": round(expected, 2),
                "subscription_expiry": o["subscription_expiry"],
                "expected": round(expected, 2),
                "collected": collected,
                "outstanding": round(expected - collected, 2),
            }
        )

    totals = {
        "expected": round(total_expected, 2),
        "collected": round(total_collected, 2),
        "outstanding": round(total_expected - total_collected, 2),
    }
    return {"totals": totals, "organizations": rows}

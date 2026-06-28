"""Reports — Phase 1: compute + JSON view + PDF download.

A single report can COMBINE several types (drivers, trips, kilometers, speed),
is org-scoped, and covers a date range (preset or custom). Scheduled email
delivery is deferred to the Firebase phase.

Planned-vs-actual km is a first-class concern: wherever km appears we show
planned, actual, and their difference so a manager can read route adherence.
"""

import io
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from auth import require_permission
from database import supabase
from routers.trips import _haversine_m  # reuse the geofence haversine

router = APIRouter(prefix="/reports", tags=["reports"])

VALID_TYPES = ["drivers", "trips", "kilometers", "speed"]

# The manager is in Egypt (UTC+2). Preset day boundaries are resolved in LOCAL
# time, then converted to UTC for querying the timestamptz columns. We use a
# FIXED +2 offset as specified; if Egypt's DST needs to be honored exactly,
# swap this for zoneinfo.ZoneInfo("Africa/Cairo").
LOCAL_TZ = timezone(timedelta(hours=2))


def _parse_types(types: str) -> list:
    requested = [t.strip() for t in (types or "").split(",") if t.strip()]
    if not requested:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provide at least one type in {VALID_TYPES} (comma-separated).",
        )
    bad = [t for t in requested if t not in VALID_TYPES]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown report type(s) {bad}. Valid: {VALID_TYPES}.",
        )
    # de-dupe, preserve canonical order
    return [t for t in VALID_TYPES if t in requested]


def _resolve_period(preset, date_from, date_to):
    """Return (d_from, d_to, label) as LOCAL calendar dates, inclusive.

    Presets (resolved against 'today' in Egypt local time):
      today -> [today, today]
      week  -> [Monday of this week, today]
      month -> [1st of this month, today]
    """
    today = datetime.now(LOCAL_TZ).date()
    if preset:
        preset = preset.lower()
        if preset == "today":
            return today, today, "today"
        if preset == "week":
            return today - timedelta(days=today.weekday()), today, "week"
        if preset == "month":
            return today.replace(day=1), today, "month"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="period preset must be one of: today, week, month.",
        )
    if not date_from or not date_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a preset (today|week|month) OR both date_from and date_to.",
        )
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date_to must be on or after date_from.",
        )
    return date_from, date_to, "custom"


def _utc_bounds(d_from: date, d_to: date):
    """Local inclusive [d_from, d_to] -> UTC half-open [start, end) for
    timestamptz filtering. End is the start of the day AFTER d_to."""
    start_local = datetime.combine(d_from, time.min, tzinfo=LOCAL_TZ)
    end_local = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _km(meters: float) -> float:
    return round(meters / 1000.0, 2)


def _build_report(org_id: str, org_name: str, types: list, d_from, d_to, label) -> dict:
    start_utc, end_utc = _utc_bounds(d_from, d_to)

    # --- Trips in the period, anchored on started_at (actual activity). Trips
    # that never started have no started_at and won't match the range. ---
    trips = (
        supabase.table("trips")
        .select("id, driver_id, route_id, vehicle_id, status, started_at, ended_at")
        .eq("org_id", org_id)
        .gte("started_at", start_utc.isoformat())
        .lt("started_at", end_utc.isoformat())
        .order("started_at", desc=False)
        .execute()
    ).data
    trip_ids = [t["id"] for t in trips]

    # Lookup maps.
    route_ids = {t["route_id"] for t in trips if t.get("route_id")}
    vehicle_ids = {t["vehicle_id"] for t in trips if t.get("vehicle_id")}
    routes = {}
    if route_ids:
        for r in (
            supabase.table("routes").select("id, name, total_km").in_("id", list(route_ids)).execute().data
        ):
            routes[r["id"]] = r
    vehicles = {}
    if vehicle_ids:
        for v in (
            supabase.table("vehicles").select("id, bus_number").in_("id", list(vehicle_ids)).execute().data
        ):
            vehicles[v["id"]] = v["bus_number"]

    # All org drivers (so per-driver sections include everyone, even 0-activity).
    drivers = {
        p["id"]: p["name"]
        for p in (
            supabase.table("profiles").select("id, name").eq("org_id", org_id).eq("role", "driver").execute().data
        )
    }

    trip_driver = {t["id"]: t.get("driver_id") for t in trips}
    trip_route = {t["id"]: t.get("route_id") for t in trips}

    # --- ONE pings query for the period's trips (filtered by trip_id, NOT a full
    # table scan), ordered by trip then time. Single pass computes both per-trip
    # actual km and per-driver speed stats. ---
    actual_m_by_trip = defaultdict(float)
    speed_by_driver = defaultdict(lambda: {"max": None, "sum": 0.0, "cnt": 0})
    if trip_ids:
        pings = (
            supabase.table("location_pings")
            .select("trip_id, lat, lng, speed, recorded_at")
            .in_("trip_id", trip_ids)
            .order("trip_id", desc=False)
            .order("recorded_at", desc=False)
            .execute()
        ).data
        prev_tid = None
        prev_lat = prev_lng = None
        for p in pings:
            tid = p["trip_id"]
            if tid != prev_tid:  # trip boundary: don't bridge distance across trips
                prev_tid, prev_lat, prev_lng = tid, None, None
            if prev_lat is not None:
                actual_m_by_trip[tid] += _haversine_m(prev_lat, prev_lng, p["lat"], p["lng"])
            prev_lat, prev_lng = p["lat"], p["lng"]
            spd = p["speed"]
            if spd is not None:
                d = trip_driver.get(tid)
                st = speed_by_driver[d]
                st["max"] = spd if st["max"] is None else max(st["max"], spd)
                st["sum"] += spd
                st["cnt"] += 1

    def actual_km(tid):
        return _km(actual_m_by_trip.get(tid, 0.0))

    def planned_km(tid):
        r = routes.get(trip_route.get(tid))
        return float(r["total_km"]) if r and r.get("total_km") is not None else 0.0

    # --- Alerts in the period, grouped by driver and type. ---
    alert_counts = defaultdict(lambda: defaultdict(int))
    for a in (
        supabase.table("alerts")
        .select("driver_id, type, occurred_at")
        .eq("org_id", org_id)
        .gte("occurred_at", start_utc.isoformat())
        .lt("occurred_at", end_utc.isoformat())
        .execute()
    ).data:
        alert_counts[a["driver_id"]][a["type"]] += 1

    report = {
        "org": {"id": org_id, "name": org_name},
        "period": {"from": d_from.isoformat(), "to": d_to.isoformat(), "preset": label},
        "sections": {},
    }

    # ---------------- DRIVERS ----------------
    if "drivers" in types:
        rows = []
        for did, dname in sorted(drivers.items(), key=lambda kv: (kv[1] or "")):
            dtrips = [t for t in trips if t.get("driver_id") == did]
            a_km = round(sum(actual_km(t["id"]) for t in dtrips), 2)
            # planned = completed trips' route total_km (per spec)
            p_km = round(
                sum(planned_km(t["id"]) for t in dtrips if t["status"] == "completed"), 2
            )
            ac = alert_counts.get(did, {})
            rows.append(
                {
                    "driver_id": did,
                    "driver_name": dname,
                    "trips": len(dtrips),
                    "actual_km": a_km,
                    "planned_km": p_km,
                    "difference_km": round(a_km - p_km, 2),
                    "alerts": {t: ac.get(t, 0) for t in ["speeding", "off_route", "short_stop", "offline"]},
                }
            )
        report["sections"]["drivers"] = rows

    # ---------------- TRIPS ----------------
    if "trips" in types:
        rows = []
        for t in trips:
            r = routes.get(t.get("route_id"), {})
            p_km = float(r["total_km"]) if r.get("total_km") is not None else None
            a_km = actual_km(t["id"])
            rows.append(
                {
                    "trip_id": t["id"],
                    "driver_name": drivers.get(t.get("driver_id")),
                    "route_name": r.get("name"),
                    "vehicle_bus_number": vehicles.get(t.get("vehicle_id")),
                    "started_at": t.get("started_at"),
                    "ended_at": t.get("ended_at"),
                    "status": t.get("status"),
                    "planned_km": p_km,
                    "actual_km": a_km,
                    "difference_km": round(a_km - (p_km or 0.0), 2),
                }
            )
        report["sections"]["trips"] = rows

    # ---------------- KILOMETERS ----------------
    if "kilometers" in types:
        def km_group(key_fn, label_fn, id_name, name_name):
            agg = defaultdict(lambda: {"planned": 0.0, "actual": 0.0})
            for t in trips:
                k = key_fn(t)
                if k is None:
                    continue
                if t["status"] == "completed":
                    agg[k]["planned"] += planned_km(t["id"])
                agg[k]["actual"] += actual_km(t["id"])
            out = []
            for k, v in agg.items():
                out.append(
                    {
                        id_name: k,
                        name_name: label_fn(k),
                        "planned_km": round(v["planned"], 2),
                        "actual_km": round(v["actual"], 2),
                        "difference_km": round(v["actual"] - v["planned"], 2),
                    }
                )
            return sorted(out, key=lambda r: (r[name_name] or ""))

        report["sections"]["kilometers"] = {
            "by_vehicle": km_group(
                lambda t: t.get("vehicle_id"), lambda k: vehicles.get(k),
                "vehicle_id", "vehicle_bus_number",
            ),
            "by_driver": km_group(
                lambda t: t.get("driver_id"), lambda k: drivers.get(k),
                "driver_id", "driver_name",
            ),
        }

    # ---------------- SPEED ----------------
    if "speed" in types:
        rows = []
        for did, dname in sorted(drivers.items(), key=lambda kv: (kv[1] or "")):
            st = speed_by_driver.get(did)
            max_speed = st["max"] if st and st["cnt"] else None
            avg_speed = round(st["sum"] / st["cnt"], 1) if st and st["cnt"] else None
            rows.append(
                {
                    "driver_id": did,
                    "driver_name": dname,
                    "max_speed": max_speed,
                    "avg_speed": avg_speed,
                    "speeding_alerts": alert_counts.get(did, {}).get("speeding", 0),
                }
            )
        report["sections"]["speed"] = rows

    return report


def _resolve_and_build(current_user, types, period, date_from, date_to):
    org_id = current_user["org_id"]
    type_list = _parse_types(types)
    d_from, d_to, label = _resolve_period(period, date_from, date_to)

    org_row = supabase.table("organizations").select("name").eq("id", org_id).limit(1).execute().data
    org_name = org_row[0]["name"] if org_row else "Organization"
    return _build_report(org_id, org_name, type_list, d_from, d_to, label)


@router.get("")
def get_report(
    current_user: dict = Depends(require_permission("view_reports")),
    types: str = Query(..., description="Comma-separated: drivers,trips,kilometers,speed"),
    period: Optional[str] = Query(None, description="Preset: today|week|month"),
    date_from: Optional[date] = Query(None, description="Custom range start (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Custom range end (YYYY-MM-DD)"),
):
    return _resolve_and_build(current_user, types, period, date_from, date_to)


@router.get("/pdf")
def get_report_pdf(
    current_user: dict = Depends(require_permission("view_reports")),
    types: str = Query(..., description="Comma-separated: drivers,trips,kilometers,speed"),
    period: Optional[str] = Query(None, description="Preset: today|week|month"),
    date_from: Optional[date] = Query(None, description="Custom range start (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Custom range end (YYYY-MM-DD)"),
):
    report = _resolve_and_build(current_user, types, period, date_from, date_to)
    pdf_bytes = _render_pdf(report)
    fname = f"report_{report['period']['from']}_{report['period']['to']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _render_pdf(report: dict) -> bytes:
    # Imported lazily so the JSON endpoint doesn't pay reportlab's import cost.
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Fleet Report")
    story = []

    org = report["org"]["name"]
    per = report["period"]
    story.append(Paragraph(f"{org} — Fleet Report", styles["Title"]))
    story.append(
        Paragraph(
            f"Period: {per['from']} to {per['to']} ({per['preset']})", styles["Normal"]
        )
    )
    story.append(Spacer(1, 12))

    def add_table(title, headers, data_rows):
        story.append(Paragraph(title, styles["Heading2"]))
        if not data_rows:
            story.append(Paragraph("No data for this period.", styles["Italic"]))
            story.append(Spacer(1, 12))
            return
        table = Table([headers] + data_rows, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f7")]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 14))

    s = report["sections"]

    if "drivers" in s:
        rows = [
            [
                r["driver_name"], r["trips"], r["planned_km"], r["actual_km"],
                r["difference_km"],
                f"sp{r['alerts']['speeding']} or{r['alerts']['off_route']} "
                f"ss{r['alerts']['short_stop']} of{r['alerts']['offline']}",
            ]
            for r in s["drivers"]
        ]
        add_table(
            "Drivers",
            ["Driver", "Trips", "Planned km", "Actual km", "Diff km", "Alerts (sp/or/ss/of)"],
            rows,
        )

    if "trips" in s:
        rows = [
            [
                r["driver_name"], r["route_name"], r["vehicle_bus_number"],
                (r["started_at"] or "")[:16], (r["ended_at"] or "")[:16],
                r["status"], r["planned_km"], r["actual_km"], r["difference_km"],
            ]
            for r in s["trips"]
        ]
        add_table(
            "Trips",
            ["Driver", "Route", "Bus", "Started", "Ended", "Status", "Plan km", "Act km", "Diff"],
            rows,
        )

    if "kilometers" in s:
        vrows = [
            [r["vehicle_bus_number"], r["planned_km"], r["actual_km"], r["difference_km"]]
            for r in s["kilometers"]["by_vehicle"]
        ]
        add_table("Kilometers — by vehicle", ["Bus", "Planned km", "Actual km", "Diff km"], vrows)
        drows = [
            [r["driver_name"], r["planned_km"], r["actual_km"], r["difference_km"]]
            for r in s["kilometers"]["by_driver"]
        ]
        add_table("Kilometers — by driver", ["Driver", "Planned km", "Actual km", "Diff km"], drows)

    if "speed" in s:
        rows = [
            [r["driver_name"], r["max_speed"], r["avg_speed"], r["speeding_alerts"]]
            for r in s["speed"]
        ]
        add_table("Speed", ["Driver", "Max km/h", "Avg km/h", "Speeding alerts"], rows)

    doc.build(story)
    return buf.getvalue()

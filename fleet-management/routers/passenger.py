"""Passenger-facing endpoints (role='passenger' only).

STRICT SCOPING (security): a passenger may ONLY see drivers currently running
their OWN route. Every query here is pinned to:
  * the passenger's org_id (from their token → profile), and
  * the passenger's route_id (from the passengers detail row).
There is no way to pass another org/route/driver id in — the passenger supplies
nothing; the server derives everything from the authenticated identity. So a
passenger can never read another route's drivers or any management data.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_role
from capacity_logic import earliest_request_date, read_cutoff, require_school_org
from database import supabase
from live_logic import LOCAL_TZ, driver_live_positions, pick_current_assignment

router = APIRouter(prefix="/passenger", tags=["passenger"])

ONLINE_WINDOW = timedelta(minutes=2)


@router.get("/my-children")
def my_children(current_user: dict = Depends(require_role("passenger"))):
    """The students linked to the signed-in PARENT (school module). One parent
    account can own several children (siblings). Feeds the upcoming parent UI's
    child picker. Derived entirely from the authenticated parent — they pass
    nothing. (For a University passenger, this simply returns their own record.)"""
    parent_id = current_user["id"]
    org_id = current_user["org_id"]

    children = (
        supabase.table("passengers")
        .select("id, name, grade, class_name, route_id, drop_off_stop")
        .eq("org_id", org_id)
        .eq("parent_id", parent_id)
        .execute()
        .data
    )
    route_ids = list({c["route_id"] for c in children if c.get("route_id")})
    routes = {}
    if route_ids:
        routes = {r["id"]: r["name"] for r in supabase.table("routes").select("id, name").in_("id", route_ids).execute().data}

    out = [
        {
            "id": c["id"],  # the child's stable student id — a future change request references this
            "name": c.get("name"),
            "grade": c.get("grade"),
            "class_name": c.get("class_name"),
            "route_id": c.get("route_id"),
            "route_name": routes.get(c.get("route_id")),
            "drop_off_stop": c.get("drop_off_stop"),  # the child's normal drop-off stop (name)
        }
        for c in children
    ]
    out.sort(key=lambda x: (x["name"] or "").lower())
    return {"count": len(out), "children": out}


@router.get("/change-options")
def change_options(current_user: dict = Depends(require_role("passenger"))):
    """Everything the parent's "request a bus change" form needs, in one call:
      * the org's routes, each with its ordered stops (for the searchable route
        dropdown + the stop dropdown filtered to that route),
      * the cutoff time, and
      * earliest_date — the first date still selectable under the SAME-DAY cutoff
        (today until the cutoff passes, else tomorrow). The picker uses this as its
        minimum so a date the server would reject is never offered.
    School orgs only."""
    org_id = current_user["org_id"]
    require_school_org(org_id)

    routes = (
        supabase.table("routes")
        .select("id, name, color, is_active")
        .eq("org_id", org_id)
        .execute()
        .data
    )
    routes = [r for r in routes if r.get("is_active", True)]
    route_ids = [r["id"] for r in routes]
    stops_by_route: dict = {}
    if route_ids:
        srows = (
            supabase.table("route_stops")
            .select("id, route_id, name, lat, lng, stop_order")
            .in_("route_id", route_ids)
            .order("stop_order", desc=False)
            .execute()
            .data
        )
        for s in srows:
            stops_by_route.setdefault(s["route_id"], []).append(
                {"id": s["id"], "name": s.get("name"), "lat": s.get("lat"), "lng": s.get("lng"), "stop_order": s.get("stop_order")}
            )

    out_routes = [
        {"id": r["id"], "name": r.get("name"), "color": r.get("color"), "stops": stops_by_route.get(r["id"], [])}
        for r in routes
    ]
    out_routes.sort(key=lambda x: (x["name"] or "").lower())

    cutoff = read_cutoff(org_id)
    return {
        "today": datetime.now(LOCAL_TZ).date().isoformat(),
        "cutoff_time": cutoff.strftime("%H:%M"),
        "earliest_date": earliest_request_date(org_id).isoformat(),
        "routes": out_routes,
    }


@router.get("/change-requests")
def my_change_requests(current_user: dict = Depends(require_role("passenger"))):
    """The signed-in PARENT's own change requests (all statuses), newest first —
    so the parent can see pending / approved / rejected. School orgs only."""
    org_id = current_user["org_id"]
    parent_id = current_user["id"]
    require_school_org(org_id)

    reqs = (
        supabase.table("change_requests")
        .select("id, student_id, current_route_id, requested_route_id, requested_stop, request_date, status, created_at, decided_at")
        .eq("org_id", org_id)
        .eq("parent_id", parent_id)
        .order("created_at", desc=True)
        .execute()
        .data
    )
    student_ids = list({r["student_id"] for r in reqs if r.get("student_id")})
    route_ids = set()
    for r in reqs:
        for k in ("current_route_id", "requested_route_id"):
            if r.get(k):
                route_ids.add(r[k])
    students = {}
    if student_ids:
        students = {p["id"]: p.get("name") for p in supabase.table("passengers").select("id, name").in_("id", student_ids).execute().data}
    rnames = {}
    if route_ids:
        rnames = {x["id"]: x["name"] for x in supabase.table("routes").select("id, name").in_("id", list(route_ids)).execute().data}

    out = [
        {
            "id": r["id"],
            "status": r["status"],
            "request_date": r["request_date"],
            "student_id": r.get("student_id"),
            "student_name": students.get(r.get("student_id")),
            "current_route_name": rnames.get(r.get("current_route_id")),
            "requested_route_name": rnames.get(r.get("requested_route_id")),
            "requested_stop": r.get("requested_stop"),
            "created_at": r.get("created_at"),
            "decided_at": r.get("decided_at"),
        }
        for r in reqs
    ]
    return {"count": len(out), "change_requests": out}


@router.get("/children/{student_id}/track")
def track_child(student_id: str, current_user: dict = Depends(require_role("passenger"))):
    """Everything needed to track ONE of the parent's children today: the child's
    EFFECTIVE route + stops, the live bus position (same source as the manager Full
    View), the supervisor (name + phone), and the bus driver (name + phone — the
    phone is meant to be visible to the parent). School orgs only; a parent can
    only see their OWN child (parent_id must match)."""
    parent_id = current_user["id"]
    org_id = current_user["org_id"]
    require_school_org(org_id)

    st = (
        supabase.table("passengers")
        .select("id, name, grade, class_name, route_id, drop_off_stop")
        .eq("id", student_id)
        .eq("org_id", org_id)
        .eq("parent_id", parent_id)  # OWN child only
        .limit(1)
        .execute()
        .data
    )
    if not st:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That child is not linked to your account.")
    child = st[0]

    # --- Today's EFFECTIVE route: an APPROVED change request for today overrides
    # the child's normal route; otherwise the normal route applies. ---
    today = datetime.now(LOCAL_TZ).date().isoformat()
    cr = (
        supabase.table("change_requests")
        .select("requested_route_id, requested_stop")
        .eq("org_id", org_id)
        .eq("student_id", student_id)
        .eq("request_date", today)
        .eq("status", "approved")
        .limit(1)
        .execute()
        .data
    )
    changed_today = bool(cr)
    effective_route_id = cr[0]["requested_route_id"] if changed_today else child.get("route_id")
    # The stop where the child gets off TODAY: the change request's requested_stop
    # when changed, otherwise the student's normal drop-off stop. Both are names.
    effective_stop = (cr[0].get("requested_stop") if changed_today else child.get("drop_off_stop")) or None

    result = {
        "child": {"id": child["id"], "name": child.get("name"), "grade": child.get("grade"), "class_name": child.get("class_name")},
        "changed_today": changed_today,
        "effective_stop": effective_stop,       # the drop-off stop NAME (normal or changed)
        "drop_off_stop": None,                   # resolved {name, lat, lng, stop_order} for the map — filled below
        "route": None,
        "bus": None,
        "supervisor": None,
        "bus_driver": None,
        "position": None,
        "online": False,
    }
    if not effective_route_id:
        return result  # no route assigned yet

    # Route + ordered stops.
    r = supabase.table("routes").select("id, name, color, geometry").eq("id", effective_route_id).eq("org_id", org_id).limit(1).execute().data
    route = r[0] if r else {}
    stops = (
        supabase.table("route_stops")
        .select("id, name, lat, lng, stop_order")
        .eq("route_id", effective_route_id)
        .order("stop_order", desc=False)
        .execute()
        .data
    )
    result["route"] = {
        "id": route.get("id"),
        "name": route.get("name"),
        "color": route.get("color"),
        "geometry": route.get("geometry"),
        "stops": stops,
    }

    # Resolve the effective drop-off stop NAME to its coordinates on this route so
    # the parent map can measure distance/ETA from the live bus to THAT stop. Match
    # case-insensitively; if the stop can't be found on the route, leave coords null
    # (the name still shows).
    if effective_stop:
        target = next((s for s in stops if (s.get("name") or "").strip().lower() == effective_stop.strip().lower()), None)
        if target:
            result["drop_off_stop"] = {
                "name": target.get("name"),
                "lat": target.get("lat"),
                "lng": target.get("lng"),
                "stop_order": target.get("stop_order"),
            }
        else:
            result["drop_off_stop"] = {"name": effective_stop, "lat": None, "lng": None, "stop_order": None}

    # Today's assignment for the effective route → supervisor (app user), bus,
    # bus driver. Pick the current one by time if there are several shifts.
    assigns = (
        supabase.table("assignments")
        .select("driver_id, vehicle_id, bus_driver_id, start_time, end_time")
        .eq("org_id", org_id)
        .eq("route_id", effective_route_id)
        .eq("trip_date", today)
        .execute()
        .data
    )
    a = pick_current_assignment(assigns, datetime.now(LOCAL_TZ).time()) if assigns else None
    supervisor_driver_id = None
    if a:
        supervisor_driver_id = a.get("driver_id")
        if supervisor_driver_id:
            sp = supabase.table("profiles").select("name, phone").eq("id", supervisor_driver_id).limit(1).execute().data
            if sp:
                result["supervisor"] = {"name": sp[0].get("name"), "phone": sp[0].get("phone")}
        if a.get("bus_driver_id"):
            bd = supabase.table("bus_drivers").select("name, phone").eq("id", a["bus_driver_id"]).eq("org_id", org_id).limit(1).execute().data
            if bd:
                result["bus_driver"] = {"name": bd[0].get("name"), "phone": bd[0].get("phone")}  # phone visible to parent
        if a.get("vehicle_id"):
            v = supabase.table("vehicles").select("bus_number, plate_number").eq("id", a["vehicle_id"]).limit(1).execute().data
            if v:
                result["bus"] = {"bus_number": v[0].get("bus_number"), "plate_number": v[0].get("plate_number")}

    # Live position: the SAME feed the manager Full View uses. Match the child's
    # bus by its supervisor (driver), falling back to the route.
    feed = driver_live_positions(org_id)
    entry = None
    if supervisor_driver_id:
        entry = next((d for d in feed if d["driver_id"] == supervisor_driver_id), None)
    if entry is None:
        entry = next((d for d in feed if d.get("route_id") == effective_route_id), None)
    if entry:
        result["position"] = entry.get("position")
        result["online"] = entry.get("online", False)
    return result


@router.get("/live")
def passenger_live(current_user: dict = Depends(require_role("passenger"))):
    """Live positions of the drivers currently assigned to (running an active
    trip on) THIS passenger's route — and nothing else."""
    org_id = current_user["org_id"]

    pax = (
        supabase.table("passengers").select("route_id").eq("id", current_user["id"]).limit(1).execute().data
    )
    route_id = pax[0]["route_id"] if pax else None
    if not route_id:
        return {"route_id": None, "count": 0, "drivers": []}

    # Active trips on the passenger's route ONLY (org + route pinned).
    active = (
        supabase.table("trips")
        .select("id, driver_id, vehicle_id, route_id")
        .eq("org_id", org_id)
        .eq("route_id", route_id)
        .eq("status", "active")
        .execute()
    ).data
    if not active:
        return {"route_id": route_id, "count": 0, "drivers": []}

    driver_ids = list({t["driver_id"] for t in active})
    vehicle_ids = list({t["vehicle_id"] for t in active if t.get("vehicle_id")})
    names = {
        p["id"]: p["name"]
        for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data
    }
    buses = {}
    if vehicle_ids:
        buses = {
            v["id"]: v["bus_number"]
            for v in supabase.table("vehicles").select("id, bus_number").in_("id", vehicle_ids).execute().data
        }

    cutoff = datetime.now(timezone.utc) - ONLINE_WINDOW
    out = []
    seen = set()
    for t in active:
        did = t["driver_id"]
        if did in seen:
            continue
        seen.add(did)
        lp = (
            supabase.table("location_pings")
            .select("lat, lng, recorded_at")
            .eq("trip_id", t["id"])
            .order("recorded_at", desc=True)
            .limit(1)
            .execute()
        ).data
        position = None
        online = False
        if lp:
            p = lp[0]
            position = {"lat": p["lat"], "lng": p["lng"], "recorded_at": p["recorded_at"]}
            try:
                rec = datetime.fromisoformat(str(p["recorded_at"]).replace("Z", "+00:00"))
                online = rec >= cutoff
            except Exception:
                online = False
        out.append(
            {
                "driver_id": did,
                "name": names.get(did),
                "vehicle_bus_number": buses.get(t.get("vehicle_id")),
                "position": position,
                "online": online,
            }
        )
    return {"route_id": route_id, "count": len(out), "drivers": out}

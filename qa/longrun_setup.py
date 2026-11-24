"""Long-offline test fixtures on PRODUCTION (or QA_BASE): a dedicated qa_
university org with a qa_ driver, a qa_ bus, a qa_ route that follows a REAL
road geometry (copied read-only from the demo route) and today's assignment.
The demo org itself is never written to.

Ledger: qa/out/longrun_ledger.json. `--cleanup` deletes the org through the API
(everything inside it), the qa_ super admin, and the ledger.

Usage: python qa/longrun_setup.py            (create / reuse)
       python qa/longrun_setup.py --cleanup
"""
import json
import sys
from datetime import datetime, timedelta

from qa_lib import CAIRO, OUT, api, auth_admin, mint_impersonation, password, sb, sb_count

LEDGER = OUT / "longrun_ledger.json"
SLUG = "qa-longrun-uni"


def load():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def save(d):
    LEDGER.write_text(json.dumps(d, indent=2), encoding="utf-8")


def ok(code, data, what):
    if code not in (200, 201):
        raise SystemExit(f"FAIL {what}: {code} {str(data)[:300]}")
    return data


def super_admin(led):
    sa = led.setdefault("super_admin", {})
    if "id" not in sa:
        pw = password()
        email = "qa_superadmin_longrun@example.com"
        u = auth_admin("POST", "users", {"email": email, "password": pw, "email_confirm": True})
        sb("POST", "super_admins", {"id": u["id"], "email": email, "name": "qa_ Super Admin (longrun)", "is_active": True, "permissions": {"view_all": True}}, prefer="return=minimal")
        sa.update(id=u["id"], email=email, password=pw)
        save(led)
    code, d, _ = api("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})
    return ok(code, d, "super-admin login")["access_token"]


def setup():
    led = load()
    sa_tok = super_admin(led)
    if "org_id" not in led:
        body = {"name": "qa_ LongRun University", "username": "qa_owner_lr", "password": password(), "slug": SLUG,
                "module": "university", "phone": "+20 100 000 0001"}
        d = ok(*api("POST", "/organizations", sa_tok, body)[:2], "create org")
        org = d.get("organization") or d.get("org") or d
        led.update(org_id=org["id"], slug=org.get("slug", SLUG))
        save(led)
    oid = led["org_id"]
    owner = sb("GET", f"profiles?select=id&org_id=eq.{oid}&role=eq.owner&limit=1")[0]
    tok = mint_impersonation(owner["id"], oid)
    if "driver" not in led:
        pw = password()
        d = ok(*api("POST", "/drivers", tok, {"name": "qa_ LongRun Driver", "username": "qa_lr_driver", "password": pw, "phone": "01000000000"})[:2], "create driver")
        led["driver"] = {"id": d.get("id") or (d.get("driver") or {}).get("id"), "username": "qa_lr_driver", "password": pw}
        save(led)
    if "manager" not in led:
        pw = password()
        d = ok(*api("POST", "/users", tok, {"name": "qa_ LongRun Manager", "username": "qa_lr_mgr", "password": pw, "role": "manager"})[:2], "create manager")
        led["manager"] = {"id": d.get("id") or (d.get("user") or {}).get("id"), "username": "qa_lr_mgr", "password": pw}
        save(led)
    if "vehicle" not in led:
        d = ok(*api("POST", "/vehicles", tok, {"bus_number": "qa_LR_BUS", "plate_number": "QA LR 100", "capacity": 30})[:2], "create vehicle")
        led["vehicle"] = {"id": d.get("id") or (d.get("vehicle") or {}).get("id")}
        save(led)
    if "route" not in led:
        demo = json.loads((OUT / "demo.json").read_text(encoding="utf-8"))
        src = sb("GET", f"routes?select=geometry,total_km,est_minutes&id=eq.{demo['route']['id']}")[0]  # read-only copy
        stops = sb("GET", f"route_stops?select=name,lat,lng,stop_order,dwell_minutes&route_id=eq.{demo['route']['id']}&order=stop_order")
        qstops = [{"name": f"qa_{s['name']}", "lat": s["lat"], "lng": s["lng"], "stop_order": s["stop_order"], "dwell_minutes": 1} for s in stops]
        d = ok(*api("POST", "/routes", tok, {"name": "qa_LongRun Route (real roads)", "stops": qstops, "geometry": src["geometry"],
                                           "total_km": src.get("total_km"), "est_minutes": src.get("est_minutes")})[:2], "create route")
        led["route"] = {"id": d.get("id") or (d.get("route") or {}).get("id"), "points": len(src["geometry"]["coordinates"]), "km": src.get("total_km")}
        save(led)
    if "assignment" not in led:
        now = datetime.now(CAIRO)
        d = ok(*api("POST", "/assignments", tok, {"driver_id": led["driver"]["id"], "route_id": led["route"]["id"], "vehicle_id": led["vehicle"]["id"],
                                                 "trip_date": now.date().isoformat(), "start_time": (now - timedelta(minutes=5)).strftime("%H:%M"),
                                                 "end_time": "23:30", "shift_label": "qa_longrun"})[:2], "create assignment")
        led["assignment"] = {"id": d.get("id") or (d.get("assignment") or {}).get("id")}
        save(led)
    print(json.dumps({k: (v if k not in ("super_admin", "driver", "manager") else {"id": v.get("id"), "username": v.get("username", v.get("email"))}) for k, v in led.items()}, indent=1))


def cleanup():
    led = load()
    if not led:
        print("nothing to clean")
        return True
    sa_tok = super_admin(led) if led.get("super_admin") else None
    if led.get("org_id") and sb("GET", f"organizations?select=id,slug,name&id=eq.{led['org_id']}"):
        row = sb("GET", f"organizations?select=slug,name&id=eq.{led['org_id']}")[0]
        assert row["slug"].startswith("qa-") and row["name"].startswith("qa_"), row
        code, d, _ = api("DELETE", f"/organizations/{led['org_id']}", sa_tok)
        print("delete org", code, str(d)[:160])
    sa = led.get("super_admin") or {}
    if sa.get("id"):
        sb("DELETE", f"super_admins?id=eq.{sa['id']}")
        try:
            auth_admin("DELETE", f"users/{sa['id']}")
        except Exception as e:
            print("auth delete super admin:", str(e)[:80])
    left = {"orgs": sb_count(f"organizations?select=id&slug=eq.{SLUG}"), "profiles": sb_count("profiles?select=id&username=like.qa_lr_*"),
            "super_admins": sb_count("super_admins?select=id&email=like.qa_superadmin_longrun*")}
    print("left:", left)
    if all(v == 0 for v in left.values()):
        LEDGER.unlink(missing_ok=True)
        return True
    return False


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        sys.exit(0 if cleanup() else 1)
    setup()

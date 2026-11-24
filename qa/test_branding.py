"""Branding (org logo) + driver schedule — API test against a LOCAL backend.

Creates TWO dedicated orgs (qa_ School Overnight / qa_ University Overnight)
with a qa_ super admin, manager, driver, vehicle, route and assignments, runs
the checks, and records every id in qa/out/overnight_ledger.json.
`--cleanup` deletes everything it created (orgs via the API's org delete,
logos in Storage, the super admin) and verifies nothing is left.

Run: QA_BASE=http://127.0.0.1:8000 python qa/test_branding.py [--cleanup]
"""
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta

import httpx

from qa_lib import API, CAIRO, OUT, api, auth_admin, evidence, mint_impersonation, password, sb, sb_count

LEDGER = OUT / "overnight_ledger.json"
RESULTS = []


def led_load():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def led_save(d):
    LEDGER.write_text(json.dumps(d, indent=2), encoding="utf-8")


def rec(case, ok, ev):
    RESULTS.append((case, ok, ev))
    print(("PASS" if ok else "FAIL"), case, "|", ev, flush=True)


def img(kind: str) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB" if kind == "jpg" else "RGBA", (256, 256), (58, 167, 109, 255))
    d = ImageDraw.Draw(im)
    d.ellipse((48, 48, 208, 208), fill=(255, 255, 255, 255))
    d.text((92, 118), "QA", fill=(28, 43, 37, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG" if kind == "jpg" else "PNG")
    return buf.getvalue()


def b64(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def setup():
    led = led_load()
    sa = led.setdefault("super_admin", {})
    if "id" not in sa:
        pw = password()
        email = "qa_superadmin_overnight@example.com"
        u = auth_admin("POST", "users", {"email": email, "password": pw, "email_confirm": True})
        sb("POST", "super_admins", {"id": u["id"], "email": email, "name": "qa_ Super Admin (overnight)", "is_active": True, "permissions": {"view_all": True}}, prefer="return=minimal")
        sa.update(id=u["id"], email=email, password=pw)
        led_save(led)
    code, d, _ = api("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})
    assert code == 200, (code, d)
    sa_tok = d["access_token"]
    led.setdefault("orgs", {})
    for mod in ("school", "university"):
        o = led["orgs"].setdefault(mod, {})
        if "org_id" not in o:
            cat_code, cat, _ = api("GET", f"/organizations/feature-catalog/{mod}", sa_tok)
            feats = [f["key"] for f in (cat.get("toggleable") or cat.get("features") or [])] if cat_code == 200 and isinstance(cat, dict) else []
            owner_pw = password()
            slug = f"qa-{mod}-overnight"
            body = {"name": f"qa_ {'School' if mod == 'school' else 'University'} Overnight", "username": "qa_owner", "password": owner_pw,
                    "slug": slug, "module": mod, "phone": "+20 100 000 0000", "enabled_features": feats}
            code, d, _ = api("POST", "/organizations", sa_tok, body)
            assert code in (200, 201), (code, d)
            org = d.get("organization") or d.get("org") or d
            o.update(org_id=org["id"], slug=org.get("slug", slug), features=feats)
            led_save(led)
        oid = o["org_id"]
        owner = sb("GET", f"profiles?select=id&org_id=eq.{oid}&role=eq.owner&limit=1")[0]
        tok = mint_impersonation(owner["id"], oid)
        sfx = "s" if mod == "school" else "u"
        if "manager" not in o:
            pw = password()
            code, d, _ = api("POST", "/users", tok, {"name": f"qa_ Manager {sfx}", "username": f"qa_mgr_{sfx}", "password": pw, "role": "manager"})
            assert code in (200, 201), (code, d)
            o["manager"] = {"id": d.get("id") or d.get("user", {}).get("id"), "login": f"qa_mgr_{sfx}@{o['slug']}", "password": pw}
            led_save(led)
        if "driver" not in o:
            pw = password()
            code, d, _ = api("POST", "/drivers", tok, {"name": f"qa_ Driver {sfx}", "username": f"qa_drv_{sfx}", "password": pw, "phone": "01000000000", "license_number": "QA-LIC-123" if mod == "university" else None})
            assert code in (200, 201), (code, d)
            o["driver"] = {"id": d.get("id") or d.get("driver", {}).get("id"), "login": f"qa_drv_{sfx}@{o['slug']}", "password": pw}
            led_save(led)
        if "vehicle" not in o:
            code, d, _ = api("POST", "/vehicles", tok, {"bus_number": f"qa_BUSB_{sfx}", "plate_number": f"QA {sfx.upper()} 777", "capacity": 30})
            assert code in (200, 201), (code, d)
            o["vehicle"] = {"id": d.get("id") or d.get("vehicle", {}).get("id")}
            led_save(led)
        if "route" not in o:
            stops = [{"name": f"qa_Stop {c}", "lat": 30.03 + i * 0.0035, "lng": 31.47 + i * 0.0045, "stop_order": i + 1, "dwell_minutes": 1} for i, c in enumerate("ABCD")]
            geom = {"type": "LineString", "coordinates": [[s["lng"], s["lat"]] for s in stops]}
            code, d, _ = api("POST", "/routes", tok, {"name": f"qa_Route {sfx}", "stops": stops, "geometry": geom, "total_km": 1.7, "est_minutes": 12})
            assert code in (200, 201), (code, d)
            o["route"] = {"id": d.get("id") or d.get("route", {}).get("id")}
            led_save(led)
        if "assignments" not in o:
            today = datetime.now(CAIRO).date()
            o["assignments"] = []
            for delta, start in ((-1, "07:30"), (0, "07:30"), (1, "07:30"), (2, "14:00")):
                day = today + timedelta(days=delta)
                code, d, _ = api("POST", "/assignments", tok, {"driver_id": o["driver"]["id"], "route_id": o["route"]["id"], "vehicle_id": o["vehicle"]["id"],
                                                              "trip_date": day.isoformat(), "start_time": start, "end_time": "09:00" if start == "07:30" else "15:30", "shift_label": "qa_shift"})
                assert code in (200, 201), (code, d)
                o["assignments"].append({"id": d.get("id") or d.get("assignment", {}).get("id"), "date": day.isoformat()})
            led_save(led)
        o["_tok"] = tok
    return led, sa_tok


def login(o, who):
    code, d, _ = api("POST", "/auth/login", body={"login": o[who]["login"], "password": o[who]["password"]})
    return code, d


def run():
    led, sa_tok = setup()
    s_org, u_org = led["orgs"]["school"], led["orgs"]["university"]
    # A plain manager has no manage_settings (403 — checked below with the
    # driver); the Settings page needs it, so grant it to the qa_ managers.
    code, d, _ = api("PUT", "/branding/logo", login(s_org, "manager")[1]["access_token"], {"data_base64": b64(img("png"), "image/png")})
    rec("manager WITHOUT manage_settings cannot upload (403)", code == 403, f"{code}")
    for o in (s_org, u_org):
        perms = (sb("GET", f"profiles?select=permissions&id=eq.{o['manager']['id']}")[0].get("permissions") or {})
        sb("PATCH", f"profiles?id=eq.{o['manager']['id']}", {"permissions": {**perms, "manage_settings": True, "view_reports": True, "manage_passengers": True}}, prefer="return=minimal")
    _, ms = login(s_org, "manager")
    _, mu = login(u_org, "manager")
    _, du = login(u_org, "driver")
    _, ds = login(s_org, "driver")
    mtok_s, mtok_u, dtok_u, dtok_s = ms["access_token"], mu["access_token"], du["access_token"], ds["access_token"]

    # ── branding: before any upload ──
    code, b, _ = api("GET", "/branding/me", mtok_s)
    rec("branding/me (no logo yet)", code == 200 and b.get("org_logo_url") is None and b.get("org_name", "").startswith("qa_"), f"{code} {b}")
    rec("login payload carries org name + logo field", "org_name" in ms["user"] and "org_logo_url" in ms["user"], f"user keys={sorted(ms['user'].keys())}")

    # ── rejected uploads ──
    big = b"\x89PNG\r\n\x1a\n" + os.urandom(1024 * 1024 + 10)
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": b64(big, "image/png")})
    rec("upload > 1 MB rejected", code == 422 and "1 MB" in str(d), f"{code} {str(d)[:120]}")
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": b64(b"GIF89a" + b"\x00" * 64, "image/gif")})
    rec("GIF rejected (PNG/JPG/SVG only)", code == 422, f"{code} {str(d)[:120]}")
    evil = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="5"/></svg>'
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": b64(evil, "image/svg+xml")})
    rec("SVG with <script> rejected", code == 422 and "script" in str(d).lower(), f"{code} {str(d)[:120]}")
    evil2 = b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><circle r="5"/></svg>'
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": b64(evil2, "image/svg+xml")})
    rec("SVG with event handler rejected", code == 422, f"{code} {str(d)[:120]}")
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": "not base64 !!!"})
    rec("garbage base64 rejected", code == 422, f"{code} {str(d)[:100]}")
    code, d, _ = api("PUT", "/branding/logo", dtok_s, {"data_base64": b64(img("png"), "image/png")})
    rec("driver cannot upload a logo (403)", code == 403, f"{code}")
    code, d, _ = api("PUT", f"/branding/orgs/{u_org['org_id']}/logo", mtok_s, {"data_base64": b64(img("png"), "image/png")})
    rec("manager cannot use the super-admin upload for another org", code in (401, 403), f"{code}")

    # ── accepted uploads ──
    code, d, _ = api("PUT", "/branding/logo", mtok_s, {"data_base64": b64(img("png"), "image/png"), "filename": "logo.png"})
    url_s = (d or {}).get("org_logo_url") if isinstance(d, dict) else None
    rec("manager uploads PNG", code == 200 and bool(url_s) and url_s.endswith(".png"), f"{code} {url_s}")
    if url_s:
        r = httpx.get(url_s, timeout=15)
        rec("public logo URL serves the image", r.status_code == 200 and r.headers.get("content-type", "").startswith("image/png"), f"{r.status_code} {r.headers.get('content-type')} {len(r.content)} B")
    code, d, _ = api("PUT", f"/branding/orgs/{u_org['org_id']}/logo", sa_tok, {"data_base64": b64(img("jpg"), "image/jpeg"), "filename": "logo.jpg"})
    url_u = (d or {}).get("org_logo_url") if isinstance(d, dict) else None
    rec("super-admin uploads JPG for the university org", code == 200 and bool(url_u) and url_u.endswith(".jpg"), f"{code} {url_u}")
    good_svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#3AA76D"/></svg>'
    code, d, _ = api("PUT", "/branding/logo", mtok_u, {"data_base64": b64(good_svg, "image/svg+xml")})
    rec("plain SVG accepted (replaces the JPG)", code == 200 and str((d or {}).get("org_logo_url", "")).endswith(".svg"), f"{code} {(d or {}).get('org_logo_url')}")
    objs = sb_list(u_org["org_id"])
    rec("replacing a logo removes the old file", len(objs) == 1, f"objects={objs}")
    code, d, _ = api("PUT", f"/branding/orgs/{u_org['org_id']}/logo", sa_tok, {"data_base64": b64(img("jpg"), "image/jpeg")})
    url_u = (d or {}).get("org_logo_url")

    # ── payloads ──
    code, l2 = login(s_org, "driver")
    rec("driver login returns the new logo URL", code == 200 and bool(url_s) and l2["user"].get("org_logo_url") == url_s, f"{l2['user'].get('org_logo_url')}")
    code, me, _ = api("GET", "/auth/me", dtok_u)
    rec("/auth/me: branding + licence fields", code == 200 and me.get("org_logo_url") == url_u and "license_number" in me and me.get("org_phone"), f"logo={me.get('org_logo_url')} lic={me.get('license_number')} phone={me.get('org_phone')}")
    code, me_s, _ = api("GET", "/auth/me", dtok_s)
    rec("/auth/me (school supervisor) has national_id key", code == 200 and "national_id" in me_s, f"keys has national_id={'national_id' in me_s}")
    vrow = sb("GET", f"vehicles?select=share_token&id=eq.{s_org['vehicle']['id']}")[0]
    code, tr, _ = api("GET", f"/track/{vrow['share_token']}")
    rec("public tracking payload: org name + logo, no phone", code == 200 and tr.get("org_logo_url") == url_s and tr.get("org_name") and "org_phone" not in tr, f"{code} {tr}")

    # ── PDFs carry the logo ──
    r = httpx.get(f"{API}/reports/pdf", params={"types": "trips", "period": "today"}, headers={"Authorization": f"Bearer {mtok_s}"}, timeout=60)
    rec("report PDF has the logo image", r.status_code == 200 and b"/Subtype /Image" in r.content, f"{r.status_code} {len(r.content)} B image={b'/Subtype /Image' in r.content}")
    r = httpx.get(f"{API}/attendance/export", params={"format": "pdf"}, headers={"Authorization": f"Bearer {mtok_s}"}, timeout=60)
    rec("attendance PDF has the logo image", r.status_code == 200 and b"/Subtype /Image" in r.content, f"{r.status_code} {len(r.content)} B image={b'/Subtype /Image' in r.content}")
    code, d, _ = api("DELETE", "/branding/logo", mtok_s)
    rec("remove logo", code == 200 and d.get("org_logo_url") is None and sb_list(s_org["org_id"]) == [], f"{code} {d}")
    r = httpx.get(f"{API}/reports/pdf", params={"types": "trips", "period": "today"}, headers={"Authorization": f"Bearer {mtok_s}"}, timeout=60)
    rec("report PDF without a logo falls back to initials", r.status_code == 200 and b"/Subtype /Image" not in r.content, f"{r.status_code} image={b'/Subtype /Image' in r.content}")
    code, tr2, _ = api("GET", f"/track/{vrow['share_token']}")
    rec("public page after removal: no logo", tr2.get("org_logo_url") is None and tr2.get("org_name"), f"{tr2}")

    # ── driver schedule ──
    today = datetime.now(CAIRO).date()
    code, sc, _ = api("GET", "/trips/my-schedule", dtok_u, params={"start": (today - timedelta(days=1)).isoformat(), "days": 7})
    days = {x["date"]: x["assignments"] for x in (sc or {}).get("days", [])} if isinstance(sc, dict) else {}
    st = {k: [a["status"] for a in v] for k, v in days.items() if v}
    ok = code == 200 and st.get((today - timedelta(days=1)).isoformat()) == ["missed"] and st.get(today.isoformat()) == ["scheduled"] and st.get((today + timedelta(days=1)).isoformat()) == ["scheduled"]
    rec("my-schedule: 7 days, statuses (missed / scheduled)", ok, f"{code} {st}")
    rec("my-schedule: route name + bus", code == 200 and days.get(today.isoformat(), [{}])[0].get("route_name", "").startswith("qa_Route") and days[today.isoformat()][0].get("vehicle_bus_number"), f"{days.get(today.isoformat())}")
    code, sc2, _ = api("GET", "/trips/my-schedule", mtok_u)
    rec("my-schedule is driver-only (manager 403)", code == 403, f"{code}")
    code, ma, _ = api("GET", "/trips/my-assignments", dtok_u)
    a0 = (ma or {}).get("assignments", [{}])[0] if isinstance(ma, dict) else {}
    rec("my-assignments: stop_count + route length/duration", code == 200 and a0.get("stop_count") == 4 and a0.get("route_total_km") == 1.7 and a0.get("route_est_minutes") == 12, f"{code} stop_count={a0.get('stop_count')} km={a0.get('route_total_km')} min={a0.get('route_est_minutes')}")
    code, rm, _ = api("GET", f"/trips/route-map/{u_org['route']['id']}", dtok_u)
    rec("route-map: total_km + est_minutes", code == 200 and rm.get("total_km") == 1.7 and rm.get("est_minutes") == 12, f"{code} {rm.get('total_km')} {rm.get('est_minutes')}")
    code, rm2, _ = api("GET", f"/trips/route-map/{s_org['route']['id']}", dtok_u)
    rec("org isolation: university driver cannot read the school route", code == 404, f"{code}")
    code, sc3, _ = api("GET", "/trips/my-schedule", dtok_s, params={"days": 40})
    rec("my-schedule validates days (max 31)", code == 422, f"{code}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    evidence(f"{'PASS' if passed == len(RESULTS) else 'FAIL'} [branding+schedule] {passed}/{len(RESULTS)} against {API}: " + "; ".join(f"{'ok' if ok else 'FAIL'} {c}" for c, ok, _ in RESULTS))
    print(f"TOTAL {passed}/{len(RESULTS)}")
    (OUT / "overnight_branding_results.json").write_text(json.dumps([{"case": c, "ok": ok, "evidence": ev} for c, ok, ev in RESULTS], indent=1, ensure_ascii=False), encoding="utf-8")
    return passed == len(RESULTS)


def sb_list(org_id):
    from database import supabase  # noqa: E402  (run from qa/ with fleet-management on sys.path)
    try:
        return [o["name"] for o in supabase.storage.from_("org-logos").list(org_id) if str(o.get("name", "")).startswith("logo-")]
    except Exception as e:
        return [f"error {e}"]


def cleanup():
    led = led_load()
    if not led:
        print("nothing to clean")
        return True
    sa = led.get("super_admin") or {}
    sa_tok = None
    if sa.get("email"):
        code, d, _ = api("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})
        sa_tok = d.get("access_token") if code == 200 else None
    for mod, o in (led.get("orgs") or {}).items():
        oid = o.get("org_id")
        if not oid:
            continue
        row = sb("GET", f"organizations?select=id,name,slug&id=eq.{oid}")
        if not row:
            print("org already gone", mod)
            continue
        assert row[0]["slug"].startswith("qa-") and row[0]["name"].startswith("qa_"), f"refusing to delete non-qa org {row[0]}"
        try:
            from database import supabase

            objs = [f"{oid}/{x['name']}" for x in supabase.storage.from_("org-logos").list(oid)]
            if objs:
                supabase.storage.from_("org-logos").remove(objs)
        except Exception as e:
            print("storage cleanup", e)
        code, d, _ = api("DELETE", f"/organizations/{oid}", sa_tok)
        print("delete org", mod, code, str(d)[:200])
    if sa.get("id"):
        sb("DELETE", f"super_admins?id=eq.{sa['id']}")
        try:
            auth_admin("DELETE", f"users/{sa['id']}")
        except Exception as e:
            print("auth delete super admin", str(e)[:80])
    left = {
        "orgs": sb_count("organizations?select=id&slug=like.qa-*-overnight"),
        "profiles": sb_count("profiles?select=id&username=like.qa_*"),
        "super_admins": sb_count("super_admins?select=id&email=like.qa_superadmin_overnight*"),
        "routes": sb_count("routes?select=id&name=like.qa_*"),
        "vehicles": sb_count("vehicles?select=id&bus_number=like.qa_*"),
    }
    print("left:", left)
    clean = all(v == 0 for v in left.values())
    evidence(f"{'PASS' if clean else 'FAIL'} [overnight cleanup] qa_ rows left: {left}")
    if clean:
        LEDGER.unlink(missing_ok=True)
    return clean


if __name__ == "__main__":
    sys.path.insert(0, str(OUT.parents[1] / "fleet-management"))
    if "--cleanup" in sys.argv:
        sys.exit(0 if cleanup() else 1)
    sys.exit(0 if run() else 1)

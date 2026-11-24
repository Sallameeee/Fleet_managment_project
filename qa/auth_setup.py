"""qa_ accounts for the session keep-alive tests, in BOTH org types, on the live
backend. Never touches the demo orgs.

  * qa_ Auth School (new school org, slug qa-auth-school): owner, manager,
    supervisor, a second supervisor (deactivation test), a parent.
  * qa_ LongRun University (made by longrun_setup.py): its manager, plus a
    second driver (deactivation test) and a student.
  * the qa_ super admin of longrun_setup.py.

Ledger: qa/out/auth_ledger.json (gitignored, local only).
`--cleanup` deletes the school org through the API (everything in it). The
university extras go with the LongRun org (longrun_setup.py --cleanup).

Usage: python qa/auth_setup.py            (create / reuse)
       python qa/auth_setup.py --cleanup
"""
import json
import sys

from qa_lib import OUT, api, password, sb, sb_count

LEDGER = OUT / "auth_ledger.json"
LR = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
SLUG = "qa-auth-school"


def load():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def save(d):
    LEDGER.write_text(json.dumps(d, indent=2), encoding="utf-8")


def ok(code, data, what):
    if code not in (200, 201):
        raise SystemExit(f"FAIL {what}: {code} {str(data)[:300]}")
    return data


def sa_token():
    sa = LR["super_admin"]
    return ok(*api("POST", "/auth/super-admin/login", body={"email": sa["email"], "password": sa["password"]})[:2], "super-admin login")["access_token"]


def staff_token(handle, pw):
    return ok(*api("POST", "/auth/login", body={"login": handle, "password": pw})[:2], f"login {handle}")["access_token"]


def setup():
    led = load()
    s = led.setdefault("school", {})
    if "org_id" not in s:
        pw = password()
        d = ok(*api("POST", "/organizations", sa_token(), {"name": "qa_ Auth School", "username": "qa_as_owner", "password": pw,
                                                          "slug": SLUG, "module": "school", "phone": "+20 100 000 0002"})[:2], "create school org")
        org = d.get("organization") or d.get("org") or d
        s.update(org_id=org["id"], slug=SLUG, owner={"username": "qa_as_owner", "password": pw})
        save(led)
    owner_tok = staff_token(f"qa_as_owner@{SLUG}", s["owner"]["password"])
    for key, uname, role in (("manager", "qa_as_mgr", "manager"),):
        if key not in s:
            pw = password()
            d = ok(*api("POST", "/users", owner_tok, {"name": "qa_ Auth Manager", "username": uname, "password": pw, "role": role})[:2], "create manager")
            s[key] = {"id": d.get("id") or (d.get("user") or {}).get("id"), "username": uname, "password": pw}
            save(led)
    for key, uname in (("sup", "qa_as_sup"), ("sup2", "qa_as_sup2")):
        if key not in s:
            pw = password()
            d = ok(*api("POST", "/drivers", owner_tok, {"name": f"qa_ Auth {key}", "username": uname, "password": pw, "phone": "01000000000"})[:2], "create " + key)
            s[key] = {"id": d.get("id") or (d.get("driver") or {}).get("id"), "username": uname, "password": pw}
            save(led)
    if not s.get("features_on"):
        # Parents need the toggleable 'passengers' feature (off for a new school org).
        ok(*api("PATCH", f"/organizations/{s['org_id']}", sa_token(), {"enabled_features": ["passengers"]})[:2], "enable passengers")
        s["features_on"] = True
        save(led)
    if "parent" not in s:
        email = "qa_as_parent@example.com"
        d = ok(*api("POST", "/passengers", owner_tok, {"name": "qa_ Auth Student", "email": email, "grade": "3", "parent_email": email})[:2], "create parent")
        s["parent"] = {"id": d.get("id") or (d.get("passenger") or {}).get("id"), "email": email, "password": d.get("default_password") or ""}
        save(led)

    u = led.setdefault("uni", {"org_id": LR["org_id"], "slug": LR["slug"]})
    lr_owner = sb("GET", f"profiles?select=id&org_id=eq.{LR['org_id']}&role=eq.owner&limit=1")[0]
    from qa_lib import mint_impersonation
    uni_tok = mint_impersonation(lr_owner["id"], LR["org_id"])
    if "driver2" not in u:
        pw = password()
        d = ok(*api("POST", "/drivers", uni_tok, {"name": "qa_ LongRun Driver 2", "username": "qa_lr_driver2", "password": pw, "phone": "01000000000"})[:2], "create uni driver2")
        u["driver2"] = {"id": d.get("id") or (d.get("driver") or {}).get("id"), "username": "qa_lr_driver2", "password": pw}
        save(led)
    if not u.get("features_on"):
        cur = sb("GET", f"organizations?select=enabled_features&id=eq.{LR['org_id']}")[0].get("enabled_features") or []
        ok(*api("PATCH", f"/organizations/{LR['org_id']}", sa_token(), {"enabled_features": sorted(set(cur) | {"passengers"})})[:2], "enable passengers (uni)")
        u["features_on"] = True
        save(led)
    if "student" not in u:
        email = "qa_lr_student@example.com"
        d = ok(*api("POST", "/passengers", uni_tok, {"name": "qa_ LongRun Student", "email": email, "university_id": "QA-LR-0001"})[:2], "create student")
        u["student"] = {"id": d.get("id") or (d.get("passenger") or {}).get("id"), "email": email, "password": d.get("default_password") or ""}
        save(led)

    sa = LR["super_admin"]
    led["dashboard_accounts"] = [
        {"label": "school owner", "kind": "manager", "login": f"qa_as_owner@{SLUG}", "password": s["owner"]["password"]},
        {"label": "school manager", "kind": "manager", "login": f"qa_as_mgr@{SLUG}", "password": s["manager"]["password"]},
        {"label": "university manager", "kind": "manager", "login": f"{LR['manager']['username']}@{LR['slug']}", "password": LR["manager"]["password"]},
        {"label": "super admin", "kind": "admin", "login": sa["email"], "password": sa["password"]},
    ]
    led["app_accounts"] = [
        {"label": "school supervisor", "kind": "staff", "login": f"qa_as_sup@{SLUG}", "password": s["sup"]["password"]},
        {"label": "school parent", "kind": "passenger", "login": s["parent"]["email"], "password": s["parent"]["password"]},
        {"label": "university driver 2", "kind": "staff", "login": f"qa_lr_driver2@{LR['slug']}", "password": u["driver2"]["password"]},
        {"label": "university student", "kind": "passenger", "login": u["student"]["email"], "password": u["student"]["password"]},
    ]
    save(led)
    print(json.dumps({"school": {k: (v if not isinstance(v, dict) else {"id": v.get("id"), "user": v.get("username") or v.get("email")}) for k, v in s.items()},
                      "uni_extra": {k: (v if not isinstance(v, dict) else {"id": v.get("id"), "user": v.get("username") or v.get("email")}) for k, v in u.items()},
                      "parent_has_password": bool(s["parent"]["password"]), "student_has_password": bool(u["student"]["password"])}, indent=1))


def cleanup():
    led = load()
    s = led.get("school") or {}
    if s.get("org_id") and sb("GET", f"organizations?select=id&id=eq.{s['org_id']}"):
        row = sb("GET", f"organizations?select=slug,name&id=eq.{s['org_id']}")[0]
        assert row["slug"].startswith("qa-") and row["name"].startswith("qa_"), row
        code, d, _ = api("DELETE", f"/organizations/{s['org_id']}", sa_token())
        print("delete school org", code, str(d)[:160])
    left = {"orgs": sb_count(f"organizations?select=id&slug=eq.{SLUG}"), "profiles": sb_count("profiles?select=id&username=like.qa_as_*")}
    print("left:", left)
    if all(v == 0 for v in left.values()):
        LEDGER.unlink(missing_ok=True)
        return True
    return False


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        sys.exit(0 if cleanup() else 1)
    setup()

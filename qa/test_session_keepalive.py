"""Session keep-alive on the LIVE backend: every role, both org types, qa_
accounts only (qa/auth_setup.py). Prints statuses, never tokens.

Per account (super admin, school owner/manager/supervisor/parent, university
manager/driver/student):
  * login returns a refresh token;
  * 5 forced expiries in a row renew silently (an invalid access token gets 401,
    the refresh answers 200, the new token works);
  * 8 parallel refreshes of one token (UI + isolate + tabs) all get one pair;
  * re-sending a token two rotations old answers 401 code=refresh_token_already_used
    and the session itself stays alive;
  * a bogus token answers 401 with a code.
Then deactivation (school supervisor 2, university driver 2): refresh and API
both answer 403 "deactivated" (the clients' one sign-out); after reactivation
the same session works again.

  --idle-save   log in once and keep the pair in qa/out/auth_idle.json (local)
  --idle-check  hours later: the expired access token is refused, the refresh
                renews it (idle dashboard / super admin past 1 hour / app closed)
"""
import concurrent.futures as cf
import hashlib
import json
import sys
import time
from datetime import datetime, timezone

from qa_lib import OUT, api, evidence

LED = json.loads((OUT / "auth_ledger.json").read_text(encoding="utf-8"))
IDLE = OUT / "auth_idle.json"
FAILS, LINES = [], []


def check(name, ok, ev=""):
    line = f"{'PASS' if ok else 'FAIL'}  {name}  {ev}"
    print(line, flush=True)
    LINES.append(line)
    if not ok:
        FAILS.append(name)


def fp(t):
    return hashlib.sha256(t.encode()).hexdigest()[:8] if t else None


def login(a):
    if a["kind"] == "admin":
        return api("POST", "/auth/super-admin/login", body={"email": a["login"], "password": a["password"]})[:2]
    if a["kind"] == "passenger":
        return api("POST", "/auth/passenger/login", body={"email": a["login"], "password": a["password"]})[:2]
    return api("POST", "/auth/login", body={"login": a["login"], "password": a["password"]})[:2]


def me(a, tok):
    return api("GET", "/auth/super-admin/me" if a["kind"] == "admin" else "/auth/me", tok)[0]


def refresh(rt):
    code, d, _ = api("POST", "/auth/refresh", body={"refresh_token": rt})
    return code, d if isinstance(d, dict) else {}


def account_suite(a):
    tag = a["label"]
    code, d = login(a)
    if code != 200:
        check(f"{tag}: login", False, f"HTTP {code} {str(d)[:120]}")
        return
    rt = d.get("refresh_token")
    check(f"{tag}: login returns a refresh token", bool(rt), f"HTTP {code}")
    if not rt:
        return
    ok5 = True
    for _ in range(5):
        c1 = me(a, "qa-expired-token")
        c2, d2 = refresh(rt)
        c3 = me(a, d2.get("access_token", ""))
        ok5 &= (c1 == 401 and c2 == 200 and c3 == 200)
        rt = d2.get("refresh_token") or rt
    check(f"{tag}: 5 forced expiries renew silently", ok5)
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: refresh(rt), range(8)))
    new = {fp(r[1].get("refresh_token")) for r in res if r[0] == 200}
    check(f"{tag}: 8 parallel refreshes of one token", all(r[0] == 200 for r in res) and len(new) == 1,
          f"statuses={sorted({r[0] for r in res})} distinct_pairs={len(new)}")
    rt = next(r[1]["refresh_token"] for r in res if r[0] == 200)
    _, b = refresh(rt)
    _, c = refresh(b["refresh_token"])
    time.sleep(11)  # past the 10 s reuse interval
    code_old, d_old = refresh(rt)  # two rotations old
    code_new, d_new = refresh(c["refresh_token"])
    check(f"{tag}: stale (grandparent) token -> 401 already_used, session alive",
          code_old == 401 and d_old.get("code") == "refresh_token_already_used" and code_new == 200,
          f"old={code_old} {d_old.get('code')} newest={code_new}")
    code_b, d_b = refresh("qa_bogus_refresh_token")
    check(f"{tag}: bogus token -> 401 with a code", code_b == 401 and bool(d_b.get("code")), f"{code_b} {d_b.get('code')}")


def deactivation_suite(label, handle, pw, driver_id, owner_tok):
    code, d = api("POST", "/auth/login", body={"login": handle, "password": pw})[:2]
    if code != 200:
        check(f"{label}: login", False, f"{code}")
        return
    at, rt = d["access_token"], d["refresh_token"]
    try:
        c, dd, _ = api("PATCH", f"/drivers/{driver_id}", owner_tok, {"is_active": False})
        check(f"{label}: deactivated by the org", c == 200, f"{c}")
        c_me, d_me, _ = api("GET", "/auth/me", at)
        c_rf, d_rf = refresh(rt)
        check(f"{label}: API call -> 403 deactivated (client signs out)", c_me == 403 and "deactivated" in str(d_me), f"{c_me}")
        check(f"{label}: refresh -> 403 deactivated (client signs out)", c_rf == 403 and "deactivated" in str(d_rf), f"{c_rf}")
    finally:
        c, _, _ = api("PATCH", f"/drivers/{driver_id}", owner_tok, {"is_active": True})
    c_rf2, d_rf2 = refresh(rt)
    check(f"{label}: reactivated -> the same session renews again", c == 200 and c_rf2 == 200, f"reactivate={c} refresh={c_rf2}")


def idle_save():
    out = {"saved_at": datetime.now(timezone.utc).isoformat(), "sessions": []}
    for a in LED["dashboard_accounts"] + LED["app_accounts"]:
        code, d = login(a)
        if code == 200 and d.get("refresh_token"):
            out["sessions"].append({"label": a["label"], "kind": a["kind"], "access": d["access_token"], "refresh": d["refresh_token"]})
        print(f"idle-save {a['label']}: HTTP {code} refresh_token={'yes' if d.get('refresh_token') else 'no'}")
    IDLE.write_text(json.dumps(out), encoding="utf-8")


def idle_check():
    data = json.loads(IDLE.read_text(encoding="utf-8"))
    idle_h = (datetime.now(timezone.utc) - datetime.fromisoformat(data["saved_at"])).total_seconds() / 3600
    for s in data["sessions"]:
        a = {"kind": s["kind"]}
        c_old = me(a, s["access"])
        c_rf, d_rf = refresh(s["refresh"])
        c_new = me(a, d_rf.get("access_token", ""))
        check(f"idle {idle_h:.1f} h, {s['label']}: old token refused, refresh renews, new token works",
              c_old == 401 and c_rf == 200 and c_new == 200, f"old={c_old} refresh={c_rf} new={c_new}")
    IDLE.unlink(missing_ok=True)


def main():
    if "--idle-save" in sys.argv:
        return idle_save()
    if "--idle-check" in sys.argv:
        idle_check()
    else:
        for a in LED["dashboard_accounts"] + LED["app_accounts"]:
            account_suite(a)
        s, u = LED["school"], LED["uni"]
        owner_tok = api("POST", "/auth/login", body={"login": f"qa_as_owner@{s['slug']}", "password": s["owner"]["password"]})[1]["access_token"]
        deactivation_suite("school supervisor 2", f"qa_as_sup2@{s['slug']}", s["sup2"]["password"], s["sup2"]["id"], owner_tok)
        lr = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
        from qa_lib import mint_impersonation, sb
        lr_owner = sb("GET", f"profiles?select=id&org_id=eq.{lr['org_id']}&role=eq.owner&limit=1")[0]["id"]
        deactivation_suite("university driver 2", f"qa_lr_driver2@{u['slug']}", u["driver2"]["password"], u["driver2"]["id"],
                           mint_impersonation(lr_owner, lr["org_id"]))
    evidence(f"[SESSION KEEPALIVE] {'PASS' if not FAILS else 'FAIL'} ({len(LINES)} checks)\n" + "\n".join("    " + l for l in LINES))
    print(f"SESSION KEEPALIVE {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failed: {FAILS})")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

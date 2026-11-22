"""Standing invariant: "the backend is up". Run at every gate, after every deploy,
and as the very last action. Exit 0 only if every check passes.

Accounts: qa/out/smoke_accounts.json if present (demo accounts after cleanup),
otherwise the qa_ fixtures in the ledger.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

from qa_lib import API, OUT, ROOT, api, load_ledger

FAILS = []


def check(name, ok, ev, warn=False):
    tag = "PASS" if ok else ("WARN" if warn else "FAIL")
    if not ok and not warn:
        FAILS.append(name)
    print(f"{tag}  {name}: {ev}", flush=True)


def accounts():
    f = OUT / "smoke_accounts.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    led = load_ledger()
    acc = []
    for slug, o in led.get("orgs", {}).items():
        acc.append({"kind": "staff", "label": f"{slug} manager", "login": f"{o['manager']['username']}@{slug}", "password": o["manager"]["password"]})
        acc.append({"kind": "staff", "label": f"{slug} driver", "login": f"{o['drv1']['username']}@{slug}", "password": o["drv1"]["password"]})
        p = o.get("passenger") or {}
        if p.get("email"):
            acc.append({"kind": "passenger", "label": f"{slug} passenger", "email": p["email"], "password": p["default_password"]})
    sa = led.get("super_admin") or {}
    if sa.get("email"):
        acc.append({"kind": "super", "label": "super-admin", "email": sa["email"], "password": sa["password"]})
    return acc


def main():
    print(f"smoke {datetime.now(timezone.utc).isoformat()[:19]}Z against {API}")
    code, root, dt = api("GET", "/")
    check("first call latency", dt < 5, f"{dt*1000:.0f} ms", warn=True)
    check("GET / 200 ok", code == 200 and isinstance(root, dict) and root.get("status") == "ok", f"{code} {str(root)[:160]}")
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "origin/main"], capture_output=True, text=True).stdout.strip()
    live = (root or {}).get("commit") if isinstance(root, dict) else None
    # The deployed BACKEND must be current: no difference under fleet-management/
    # between the live commit and origin/main (qa/docs-only commits need no deploy).
    same_code = bool(live) and subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--quiet", live, "origin/main", "--", "fleet-management"],
        capture_output=True).returncode == 0
    check("deployed backend == origin/main backend", same_code, f"live={live} origin/main={head[:12]}")
    sw = (root or {}).get("sweeper") or {}
    last = sw.get("last_sweep_at")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else None
    check("sweeper alive (last sweep < 90 s)", bool(sw.get("running")) and age is not None and age < 90,
          f"running={sw.get('running')} last_sweep_age={None if age is None else round(age)}s last_error_at={sw.get('last_error_at')}")
    code, d, dt = api("GET", "/openapi.json")
    n = len(d.get("paths", {})) if isinstance(d, dict) else 0
    check("openapi route count", code == 200 and n == 107, f"{code} paths={n} (code: 107)")
    code, d, dt = api("GET", "/track/qa_smoke_no_such_token")
    check("DB round-trip via API", code == 404 and dt < 2, f"{code} {d} {dt*1000:.0f} ms")
    code, d, dt = api("GET", "/dashboard/summary")
    check("protected route without token -> 401", code == 401, f"{code}")
    for a in accounts():
        if a["kind"] == "staff":
            code, d, dt = api("POST", "/auth/login", body={"login": a["login"], "password": a["password"]})
        elif a["kind"] == "passenger":
            code, d, dt = api("POST", "/auth/passenger/login", body={"email": a["email"], "password": a["password"]})
        else:
            code, d, dt = api("POST", "/auth/super-admin/login", body={"email": a["email"], "password": a["password"]})
        check(f"login {a['label']}", code == 200 and dt < 1.5, f"{code} {dt*1000:.0f} ms")
    print(f"SMOKE {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failed: {FAILS})")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

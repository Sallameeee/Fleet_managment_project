"""Probe Supabase refresh-token rotation on the LIVE project with a qa_ account,
to pin down exactly when a reused refresh token kills a session.

Uses the qa_ LongRun manager (qa/out/longrun_ledger.json). Every login here makes
a NEW session of that qa_ user, so nothing else (the qa_ driver on Nox, the demo
accounts) is touched. Prints statuses and error codes only, never a token.

Usage: python qa/auth_probe.py
"""
import concurrent.futures as cf
import hashlib
import json
import time

import httpx

from qa_lib import ENV, OUT, SB_URL, api, evidence

LED = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
HANDLE = f"{LED['manager']['username']}@{LED['slug']}"
ANON = ENV["SUPABASE_ANON_KEY"]
gt = httpx.Client(timeout=30)


def fp(tok):  # short fingerprint for equality checks; never the token itself
    return hashlib.sha256(tok.encode()).hexdigest()[:8] if tok else None


def login():
    code, d, _ = api("POST", "/auth/login", body={"login": HANDLE, "password": LED["manager"]["password"]})
    assert code == 200, (code, str(d)[:200])
    return d["access_token"], d["refresh_token"]


def refresh(rt):
    """Straight to GoTrue, so its own error code is visible."""
    r = gt.post(f"{SB_URL}/auth/v1/token", params={"grant_type": "refresh_token"},
                headers={"apikey": ANON, "Content-Type": "application/json"}, json={"refresh_token": rt})
    try:
        b = r.json()
    except Exception:
        b = {}
    return r.status_code, (b.get("error_code") or b.get("code") or b.get("error")), b.get("refresh_token")


def show(label, res, expect_rt=None):
    code, err, new = res
    same = "" if expect_rt is None else f" returned_current_token={fp(new) == fp(expect_rt)}"
    line = f"{label}: HTTP {code} {err or ''} new={fp(new)}{same}"
    print(line, flush=True)
    return line


lines = []
print("E1  reuse of the PARENT of the active token")
_, rt0 = login()
c, e, rt1 = refresh(rt0)
lines.append(show("  rt0 -> rt1", (c, e, rt1)))
lines.append(show("  rt0 again at +0 s (inside the 10 s reuse interval)", refresh(rt0), rt1))
time.sleep(12)
lines.append(show("  rt0 again at +12 s (outside the interval, rt0 = parent of active)", refresh(rt0), rt1))
lines.append(show("  rt1 still usable afterwards?", refresh(rt1)))

print("E2  reuse of a GRANDPARENT token (the other isolate refreshed twice)")
_, g0 = login()
c, e, g1 = refresh(g0)
lines.append(show("  g0 -> g1", (c, e, g1)))
time.sleep(1)
c, e, g2 = refresh(g1)
lines.append(show("  g1 -> g2", (c, e, g2)))
time.sleep(12)
lines.append(show("  g0 again at +12 s (grandparent)", refresh(g0)))
lines.append(show("  g2 (the newest token) afterwards", refresh(g2)))

print("E3  6 parallel refreshes of one token (UI + isolate + tabs at once)")
_, p0 = login()
with cf.ThreadPoolExecutor(6) as ex:
    res = list(ex.map(lambda _: refresh(p0), range(6)))
codes = [r[0] for r in res]
fps = sorted({fp(r[2]) for r in res if r[2]})
lines.append(f"  statuses={codes} distinct_new_tokens={len(fps)}")
print(lines[-1])

print("E4  backend /auth/refresh with a bogus token")
code, d, _ = api("POST", "/auth/refresh", body={"refresh_token": "qa_bogus_token"})
lines.append(f"  backend: HTTP {code} {d}")
print(lines[-1])

evidence("[AUTH-PROBE] live GoTrue rotation behaviour (qa_ manager sessions):\n" + "\n".join("    " + l for l in lines))

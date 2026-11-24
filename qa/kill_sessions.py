"""End EVERY session of one qa_ account on the server (Supabase global sign-out),
to test what the app / dashboard do when their session is really gone. Only
qa_ accounts of the LongRun org are accepted.

Usage: python qa/kill_sessions.py qa_lr_driver
"""
import json
import sys

import httpx

from qa_lib import ENV, OUT, SB_URL, api

name = sys.argv[1]
assert name.startswith("qa_"), "qa_ accounts only"
led = json.loads((OUT / "longrun_ledger.json").read_text(encoding="utf-8"))
acct = led["driver"] if led["driver"]["username"] == name else led["manager"] if led["manager"]["username"] == name else None
assert acct, f"{name} is not in the LongRun ledger"
code, d, _ = api("POST", "/auth/login", body={"login": f"{name}@{led['slug']}", "password": acct["password"]})
assert code == 200, (code, str(d)[:200])
r = httpx.post(f"{SB_URL}/auth/v1/logout", params={"scope": "global"},
               headers={"apikey": ENV["SUPABASE_ANON_KEY"], "Authorization": "Bearer " + d["access_token"]}, timeout=20)
print(f"global sign-out of {name}: HTTP {r.status_code}")
c2, d2, _ = api("POST", "/auth/refresh", body={"refresh_token": d["refresh_token"]})
print(f"a refresh token of that account afterwards: HTTP {c2} {d2}")

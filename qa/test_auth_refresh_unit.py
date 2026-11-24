"""Offline unit checks for POST /auth/refresh and the login endpoints: Supabase
Auth is mocked, nothing leaves this machine.

Pins the contract the app and the dashboard rely on so a transient problem can
never end a session:
  200 tokens (+ the rotated refresh token), 401 + "code" for an unusable refresh
  token, 403 only for a deactivated account, 503 for anything upstream/transient.

Usage: python qa/test_auth_refresh_unit.py   (exit 0 = all pass)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fleet-management"))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.auth as ra  # noqa: E402

FAILS = []


def check(name, ok, ev=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {ev}", flush=True)
    if not ok:
        FAILS.append(name)


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table = db, table

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        v = self.db.get(self.table, [])
        if isinstance(v, Exception):
            raise v
        return SimpleNamespace(data=v)


class FakeSupabase:
    def __init__(self, **tables):
        self.db = tables

    def table(self, name):
        return FakeQuery(self.db, name)


def gotrue(status, body):
    req = httpx.Request("POST", "https://example.supabase.co/auth/v1/token")
    return httpx.Response(status, json=body, request=req)


OK_BODY = {"access_token": "at-new", "refresh_token": "rt-new", "token_type": "bearer", "expires_in": 3600,
           "expires_at": 1999999999, "user": {"id": "u1"}}


def run(responses, sb, path="/auth/refresh", body=None):
    """responses: list of httpx.Response or Exception, consumed per Supabase call."""
    calls = []

    def fake_post(*_a, **_k):
        calls.append(1)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    ra.httpx.post = fake_post
    ra.supabase = sb
    app = FastAPI()
    app.include_router(ra.router)
    res = TestClient(app).post(path, json=body or {"refresh_token": "rt-old"})
    try:
        data = res.json()
    except Exception:
        data = res.text
    return res.status_code, data, len(calls)


active = FakeSupabase(profiles=[{"is_active": True}])

c, d, n = run([gotrue(200, OK_BODY)], active)
check("200 returns the rotated pair", c == 200 and d["refresh_token"] == "rt-new" and d["access_token"] == "at-new" and d["expires_at"] == 1999999999, f"{c}")

c, d, n = run([gotrue(400, {"code": 400, "error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"})], active)
check("already used -> 401 code=refresh_token_already_used", c == 401 and d.get("code") == "refresh_token_already_used", f"{c} {d}")

c, d, n = run([gotrue(400, {"error": "invalid_grant", "error_description": "Invalid Refresh Token: Already Used"})], active)
check("old Auth wording -> code=refresh_token_already_used", c == 401 and d.get("code") == "refresh_token_already_used", f"{c} {d}")

c, d, n = run([gotrue(400, {"code": 400, "error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token: Refresh Token Not Found"})], active)
check("unknown token -> 401 code=refresh_token_not_found", c == 401 and d.get("code") == "refresh_token_not_found" and d.get("detail") == "Could not refresh session.", f"{c} {d}")

for st in (500, 502, 503, 504):
    c, d, n = run([gotrue(st, {"msg": "upstream"})], active)
    check(f"Supabase {st} -> 503 (not 401)", c == 503 and d.get("code") == "auth_unavailable", f"{c} {d}")

c, d, n = run([gotrue(429, {"msg": "rate limited"})], active)
check("Supabase 429 -> 503 code=auth_rate_limited", c == 503 and d.get("code") == "auth_rate_limited", f"{c} {d}")

c, d, n = run([httpx.ConnectError("boom")], active)
check("connection error -> retried once, then 503", c == 503 and n == 2, f"{c} calls={n}")

c, d, n = run([httpx.ReadTimeout("slow"), gotrue(200, OK_BODY)], active)
check("timeout then success -> 200 (retry delivered the pair)", c == 200 and d["refresh_token"] == "rt-new" and n == 2, f"{c} calls={n}")

c, d, n = run([gotrue(200, {"oops": True, "user": {}})], active)
check("200 without tokens is passed through (client treats as failure)", c == 200, f"{c}")

broken = FakeSupabase(profiles=httpx.RemoteProtocolError("Server disconnected"), super_admins=httpx.RemoteProtocolError("x"))
c, d, n = run([gotrue(200, OK_BODY)], broken)
check("profile lookup fails AFTER rotation -> still 200 with the new pair", c == 200 and d["refresh_token"] == "rt-new", f"{c}")

c, d, n = run([gotrue(200, OK_BODY)], FakeSupabase(profiles=RuntimeError("weird")))
check("non-transient lookup error -> still 200 (fail-open, API re-checks)", c == 200, f"{c}")

c, d, n = run([gotrue(200, OK_BODY)], FakeSupabase(profiles=[{"is_active": False}]))
check("deactivated org user -> 403", c == 403 and "deactivated" in d.get("detail", ""), f"{c} {d}")

c, d, n = run([gotrue(200, OK_BODY)], FakeSupabase(profiles=[], super_admins=[{"is_active": False}]))
check("deactivated super admin -> 403", c == 403, f"{c} {d}")

c, d, n = run([gotrue(200, OK_BODY)], FakeSupabase(profiles=[], super_admins=[{"is_active": True}]))
check("active super admin -> 200", c == 200, f"{c}")

sa = FakeSupabase(super_admins=[{"id": "u1", "name": "qa", "email": "qa@example.com"}])
c, d, n = run([gotrue(200, OK_BODY)], sa, "/auth/super-admin/login", {"email": "qa@example.com", "password": "x"})
check("super-admin login returns a refresh_token", c == 200 and d.get("refresh_token") == "rt-new", f"{c}")

c, d, n = run([gotrue(503, {})], sa, "/auth/super-admin/login", {"email": "qa@example.com", "password": "x"})
check("super-admin login: Supabase 503 -> 503, not 'invalid password'", c == 503, f"{c} {d}")

c, d, n = run([gotrue(400, {"error_code": "invalid_credentials"})], sa, "/auth/super-admin/login", {"email": "qa@example.com", "password": "x"})
check("super-admin login: wrong password -> 401", c == 401, f"{c}")

org = FakeSupabase(organizations=[{"id": "o1", "status": "active"}],
                   profiles=[{"id": "u1", "name": "n", "role": "driver", "org_id": "o1", "permissions": {}, "is_active": True, "email": "e@x"}])
c, d, n = run([gotrue(502, {})], org, "/auth/login", {"login": "qa_user@qa-org", "password": "x"})
check("org login: Supabase 502 -> 503, not 'invalid password'", c == 503, f"{c} {d}")

c, d, n = run([gotrue(502, {})], org, "/auth/passenger/login", {"email": "e@x", "password": "x"})
check("passenger login: Supabase 502 -> 503", c == 503, f"{c} {d}")

print(f"AUTH UNIT {'PASS' if not FAILS else 'FAIL'} ({len(FAILS)} failed: {FAILS})")
sys.exit(1 if FAILS else 0)

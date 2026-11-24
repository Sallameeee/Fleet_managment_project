"""Shared helpers for the RouteMind QA harness (no secrets in this file).

Config comes from fleet-management/.env (Supabase) and env vars:
  QA_BASE   backend under test (default: the live Railway URL)
Outputs (ledger, evidence) go to qa/out/, which is gitignored.
"""
import json
import os
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import jwt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa" / "out"
OUT.mkdir(parents=True, exist_ok=True)
LEDGER = OUT / os.getenv("QA_LEDGER", "fixtures.json")
S_SLUG = os.getenv("QA_SCHOOL_SLUG", "school-test")  # school org used by the suites
U_SLUG = os.getenv("QA_UNI_SLUG", "acme-bus-co")  # university org used by the suites
CAIRO = ZoneInfo("Africa/Cairo")
API = os.getenv("QA_BASE", "https://fleetmanagmentproject-production-1bd7d791.up.railway.app").rstrip("/")


def _env() -> dict:
    env = {}
    for line in (ROOT / "fleet-management" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = _env()
SB_URL = ENV["SUPABASE_URL"].rstrip("/")
for _s in ("/rest/v1", "/rest"):
    if SB_URL.endswith(_s):
        SB_URL = SB_URL[: -len(_s)]
SB_KEY = ENV["SUPABASE_SERVICE_KEY"]
_SB_H = {"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY}

client = httpx.Client(timeout=40)


# ── backend API ──────────────────────────────────────────────────────────────
def api(method: str, path: str, token: str | None = None, body=None, params=None):
    """Call the backend. Returns (status, json_or_text, elapsed_s)."""
    h = {"Authorization": "Bearer " + token} if token else {}
    t0 = time.perf_counter()
    r = client.request(method, API + path, headers=h, json=body, params=params)
    dt = time.perf_counter() - t0
    try:
        data = r.json()
    except Exception:
        data = r.text[:300]
    return r.status_code, data, dt


# ── Supabase (service role, for evidence + fixtures the API can't make) ─────
def sb(method: str, path: str, body=None, prefer: str | None = None):
    h = dict(_SB_H)
    if prefer:
        h["Prefer"] = prefer
    r = client.request(method, SB_URL + "/rest/v1/" + path, headers=h, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"supabase {method} {path} -> {r.status_code} {r.text[:200]}")
    return r.json() if r.text else None


def sb_count(path: str) -> int:
    r = client.get(SB_URL + "/rest/v1/" + path, headers={**_SB_H, "Prefer": "count=exact", "Range": "0-0"})
    return int(r.headers.get("Content-Range", "*/0").split("/")[-1])


def auth_admin(method: str, path: str, body=None):
    r = client.request(method, SB_URL + "/auth/v1/admin/" + path, headers=_SB_H, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"auth admin {method} {path} -> {r.status_code} {r.text[:200]}")
    return r.json() if r.text else None


def mint_impersonation(owner_id: str, org_id: str) -> str:
    """Same token the backend's super-admin impersonation mints. The server
    signs with IMPERSONATION_SECRET or, when that is unset, the service key."""
    secret = os.getenv("IMPERSONATION_SECRET") or SB_KEY
    now = int(time.time())
    return jwt.encode({"sub": owner_id, "org_id": org_id, "act": "qa-harness", "imp": True,
                       "iat": now, "exp": now + 1800}, secret, algorithm="HS256")


# ── misc ─────────────────────────────────────────────────────────────────────
def password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Qa-" + "".join(secrets.choice(alphabet) for _ in range(14)) + "9!"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def cairo(dt_str: str) -> str:
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return dt.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M:%S %Z")


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def save_ledger(d: dict) -> None:
    LEDGER.write_text(json.dumps(d, indent=2), encoding="utf-8")


EVIDENCE = OUT / "EVIDENCE.md"


def evidence(line: str) -> None:
    stamp = datetime.now(CAIRO).strftime("%H:%M:%S")
    with EVIDENCE.open("a", encoding="utf-8") as f:
        f.write(f"- [{stamp} Cairo] {line}\n")
    print(line, flush=True)


def offset(minutes: float = 0, seconds: float = 0) -> datetime:
    return utc_now() + timedelta(minutes=minutes, seconds=seconds)

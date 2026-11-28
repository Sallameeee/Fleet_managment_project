"""Record 422s on the driver's TRACKING endpoints in the trip decision log.

A payload mismatch between an installed APK and the live backend fails in
FastAPI's validation layer BEFORE any endpoint code runs, so it used to leave
no trace at all: the app kept its buffer, retried forever, and the server
stored nothing (one of the hypotheses for the 24 Nov trips). This handler keeps
the normal 422 response and, when the caller is the trip's own driver, writes a
`request_rejected` event with what failed (field path, error, a short preview
of the offending value) — never the raw body or the token.

Only the tracking paths are inspected; every other 422 is untouched.
"""

from __future__ import annotations

import re

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

_TRACKING = re.compile(r"^/trips/([0-9a-fA-F-]{36})/(pings|heartbeat|app-state|events|stop-visits)$")
MAX_ERRORS = 20


def _preview(v) -> str:
    s = repr(v)
    return s if len(s) <= 80 else s[:77] + "..."


def _record(path_kind: str, trip_id: str, auth_header: str, errors: list) -> None:
    import auth
    import trip_events
    from database import supabase

    if not auth_header.lower().startswith("bearer "):
        return
    try:
        user = auth._verify_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials=auth_header[7:].strip()))
    except Exception:
        return  # not a valid caller: nothing is logged on their behalf
    trips = (supabase.table("trips").select("id, org_id, driver_id, status").eq("id", trip_id)
             .eq("driver_id", user.id).limit(1).execute().data)
    if not trips:
        return
    trip = trips[0]
    summary = [{"loc": [str(x) for x in e.get("loc", [])][:8], "msg": str(e.get("msg", ""))[:160],
                "type": str(e.get("type", ""))[:60], "input": _preview(e.get("input"))} for e in errors[:MAX_ERRORS]]
    first = summary[0] if summary else {}
    trip_events.emit(trip, "request_rejected",
                     f"POST {path_kind} rejected (422): {'.'.join(first.get('loc', []))} — {first.get('msg', '')}",
                     {"endpoint": path_kind, "http_status": 422, "errors": summary, "error_count": len(errors),
                      "trip_status": trip.get("status")})


async def handler(request: Request, exc: RequestValidationError):
    m = _TRACKING.match(request.url.path)
    if m and request.method == "POST":
        try:
            await run_in_threadpool(_record, m.group(2), m.group(1), request.headers.get("authorization", ""),
                                    list(exc.errors()))
        except Exception:
            pass  # logging must never change the response
    return await request_validation_exception_handler(request, exc)

"""Supabase client setup.

Loads credentials from .env and exposes a single, shared Supabase client
(`supabase`) that other modules can import:

    from database import supabase
"""

import os

from dotenv import load_dotenv
from supabase import Client, create_client

# Load variables from the local .env file into the environment.
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError(
        "Missing Supabase credentials. Make sure SUPABASE_URL and "
        "SUPABASE_SERVICE_KEY are set in your .env file."
    )

# The supabase client expects the bare project URL (https://<ref>.supabase.co)
# and appends paths like /rest/v1 itself. If the .env value includes a
# trailing REST path, strip it so the client builds correct URLs.
SUPABASE_URL = SUPABASE_URL.rstrip("/")
for suffix in ("/rest/v1", "/rest"):
    if SUPABASE_URL.endswith(suffix):
        SUPABASE_URL = SUPABASE_URL[: -len(suffix)]
        break

# Shared client, created once at import time, using the service-role key.
# The service key bypasses Row Level Security — keep it server-side only.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# Transient transport failures between this API and Supabase, seen under
# concurrent load (many buses flushing at once): "Server disconnected",
# "[Errno 32] Broken pipe". Hot paths retry the SAME idempotent call a couple of
# times and, if it still fails, answer 503 (retry later) instead of a
# misleading 400/401 — the app keeps its buffer and retries either way.
_TRANSIENT_MARKERS = (
    "server disconnected", "broken pipe", "connection reset", "connection aborted",
    "remoteprotocolerror", "readerror", "writeerror", "connecterror", "eof occurred",
    # Supabase's edge answered with an HTML error page (the request never reached
    # PostgREST, whose errors are JSON): "400 Bad Request", 502/503/504 pages.
    "<title>400 bad request</title>", "bad gateway", "gateway timeout", "service temporarily unavailable",
)


def is_transient(exc: BaseException) -> bool:
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return True
    except Exception:
        pass
    if isinstance(exc, (BrokenPipeError, ConnectionError)):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in _TRANSIENT_MARKERS)


def retry_transient(fn, tries: int = 3, base_delay: float = 0.2):
    """Run fn(); on a TRANSIENT transport error retry (0.2 s, 0.4 s). Only for
    idempotent calls (reads, upserts that ignore duplicates)."""
    import time

    for attempt in range(tries):
        try:
            return fn()
        except Exception as exc:
            if attempt == tries - 1 or not is_transient(exc):
                raise
            time.sleep(base_delay * (2 ** attempt))

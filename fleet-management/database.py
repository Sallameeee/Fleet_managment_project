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

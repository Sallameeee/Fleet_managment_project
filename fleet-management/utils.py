"""Small shared helpers."""

import re

SYNTHETIC_EMAIL_DOMAIN = "fleet.local"

# The full permission vocabulary. One source of truth for owners (all true),
# role defaults, and the privilege-escalation guard in POST /users.
PERMISSION_KEYS = [
    "manage_organization",
    "manage_users",
    "manage_drivers",
    "manage_vehicles",
    "manage_devices",
    "manage_routes",
    "manage_trips",
    "view_tracking",
    "view_reports",
    "manage_billing",
    "manage_settings",
]


def slugify(text: str) -> str:
    """Turn an org name into a URL/handle-safe slug.

        "Acme Bus Co" -> "acme-bus-co"

    Lowercase, non-alphanumeric runs collapse to single hyphens, trimmed.
    Returns "" if nothing usable remains (caller should fall back).
    """
    s = (text or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def synthesize_login_email(username: str, org_id: str) -> str:
    """Build an org-scoped synthetic login email.

    Supabase Auth emails are GLOBALLY unique, but usernames only need to be
    unique within an org. Embedding a short slice of the org_id lets the same
    username exist across different orgs without colliding in Auth.

        synthesize_login_email("john_d", "c5586c19-a8a1-4321-...") ->
            "john_d.c5586c19@fleet.local"

    This synthesized address is stored on the profile (profiles.email), so
    login looks it up rather than reconstructing it.
    """
    short_org = str(org_id)[:8]
    return f"{username}.{short_org}@{SYNTHETIC_EMAIL_DOMAIN}"

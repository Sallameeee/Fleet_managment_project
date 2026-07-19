"""In-app notifications (School module).

One set of endpoints serves BOTH audiences, scoped by the caller's role:
  * a PARENT (role='passenger') sees audience='parent' notes addressed to them;
  * a MANAGER (any staff role) sees the org-wide audience='manager' notes.
Drivers and University orgs get an empty list (never an error), so the bell can
poll harmlessly everywhere.
"""

from fastapi import APIRouter, Depends

from auth import get_current_user
from capacity_logic import org_module
from database import supabase

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _enabled(user: dict) -> bool:
    """Notifications exist only for school parents + school managers."""
    if org_module(user.get("org_id")) != "school":
        return False
    return user.get("role") != "driver"


def _scope(query, user: dict):
    """Pin a notifications query to the caller's own scope."""
    q = query.eq("org_id", user["org_id"])
    if user.get("role") == "passenger":
        return q.eq("audience", "parent").eq("recipient_id", user["id"])
    return q.eq("audience", "manager")  # org-wide for managers


@router.get("")
def list_notifications(current_user: dict = Depends(get_current_user)):
    """The caller's notifications, UNREAD FIRST then newest, plus the unread count."""
    if not _enabled(current_user):
        return {"count": 0, "unread": 0, "notifications": []}
    rows = (
        _scope(supabase.table("notifications").select("*"), current_user)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
        .data
    )
    rows.sort(key=lambda r: 1 if r.get("is_read") else 0)  # stable → unread first, newest within
    unread = sum(1 for r in rows if not r.get("is_read"))
    return {"count": len(rows), "unread": unread, "notifications": rows}


@router.get("/unread-count")
def unread_count(current_user: dict = Depends(get_current_user)):
    if not _enabled(current_user):
        return {"unread": 0}
    rows = _scope(supabase.table("notifications").select("id"), current_user).eq("is_read", False).execute().data
    return {"unread": len(rows)}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Mark one notification read (only within the caller's own scope)."""
    if not _enabled(current_user):
        return {"id": notification_id, "is_read": True}
    _scope(
        supabase.table("notifications").update({"is_read": True}).eq("id", notification_id),
        current_user,
    ).execute()
    return {"id": notification_id, "is_read": True}


@router.post("/read-all")
def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark all of the caller's notifications read."""
    if not _enabled(current_user):
        return {"updated": True}
    _scope(
        supabase.table("notifications").update({"is_read": True}).eq("is_read", False),
        current_user,
    ).execute()
    return {"updated": True}

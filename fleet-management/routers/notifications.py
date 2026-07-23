"""In-app notifications (School module).

One set of endpoints serves ALL audiences, scoped by the caller's role:
  * a PARENT (role='passenger') sees audience='parent' notes addressed to them;
    read state is per-recipient on notifications.is_read.
  * a SUPERVISOR (role='driver') sees audience='supervisor' notes addressed to
    them — same personal-inbox model as a parent (roster changes for their bus).
  * a MANAGER (any other staff role) sees the org-wide audience='manager' notes;
    read state is PER-MANAGER via the notification_reads receipt table (so one
    manager reading does NOT clear it for the others).
University orgs get an empty list (never an error).
"""

from fastapi import APIRouter, Depends

from auth import get_current_user
from capacity_logic import org_module
from database import supabase

router = APIRouter(prefix="/notifications", tags=["notifications"])

_MGR_SCAN_LIMIT = 500  # cap manager rows scanned for counting/mark-all


def _enabled(user: dict) -> bool:
    """School orgs only. All three audiences are served now (parents, managers and
    supervisors); University orgs still get an empty list, never an error."""
    return org_module(user.get("org_id")) == "school"


def _audience_of(user: dict) -> str:
    """Which inbox this caller reads. Order matters: a supervisor is a driver-role
    user and must be classified BEFORE the staff/manager fallback."""
    role = user.get("role")
    if role == "passenger":
        return "parent"
    if role == "driver":
        return "supervisor"
    return "manager"


def _is_manager(user: dict) -> bool:
    return _audience_of(user) == "manager"


def _manager_read_ids(user_id: str, notif_ids: list) -> set:
    """The subset of `notif_ids` this manager has already read (receipt rows)."""
    if not notif_ids:
        return set()
    rr = (
        supabase.table("notification_reads")
        .select("notification_id")
        .eq("user_id", user_id)
        .in_("notification_id", notif_ids)
        .execute()
        .data
    )
    return {x["notification_id"] for x in rr}


@router.get("")
def list_notifications(current_user: dict = Depends(get_current_user)):
    """The caller's notifications, UNREAD FIRST then newest, plus the unread count."""
    if not _enabled(current_user):
        return {"count": 0, "unread": 0, "notifications": []}
    org_id, uid = current_user["org_id"], current_user["id"]

    if _is_manager(current_user):
        rows = (
            supabase.table("notifications").select("*").eq("org_id", org_id).eq("audience", "manager")
            .order("created_at", desc=True).limit(100).execute().data
        )
        read_ids = _manager_read_ids(uid, [r["id"] for r in rows])
        for r in rows:
            r["is_read"] = r["id"] in read_ids  # per-manager read state
    else:
        # Personal inbox — parent OR supervisor; read state on the row itself.
        rows = (
            supabase.table("notifications").select("*").eq("org_id", org_id)
            .eq("audience", _audience_of(current_user)).eq("recipient_id", uid)
            .order("created_at", desc=True).limit(100).execute().data
        )
    rows.sort(key=lambda r: 1 if r.get("is_read") else 0)  # stable → unread first
    unread = sum(1 for r in rows if not r.get("is_read"))
    return {"count": len(rows), "unread": unread, "notifications": rows}


@router.get("/unread-count")
def unread_count(current_user: dict = Depends(get_current_user)):
    if not _enabled(current_user):
        return {"unread": 0}
    org_id, uid = current_user["org_id"], current_user["id"]
    if _is_manager(current_user):
        ids = [
            r["id"]
            for r in supabase.table("notifications").select("id").eq("org_id", org_id).eq("audience", "manager")
            .order("created_at", desc=True).limit(_MGR_SCAN_LIMIT).execute().data
        ]
        read_ids = _manager_read_ids(uid, ids)
        return {"unread": sum(1 for i in ids if i not in read_ids)}
    rows = (
        supabase.table("notifications").select("id").eq("org_id", org_id)
        .eq("audience", _audience_of(current_user)).eq("recipient_id", uid).eq("is_read", False).execute().data
    )
    return {"unread": len(rows)}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    """Mark one notification read for THIS caller (per-manager receipt; per-parent
    flag)."""
    if not _enabled(current_user):
        return {"id": notification_id, "is_read": True}
    org_id, uid = current_user["org_id"], current_user["id"]
    if _is_manager(current_user):
        # Only for a manager notification in this org.
        n = supabase.table("notifications").select("id").eq("id", notification_id).eq("org_id", org_id).eq("audience", "manager").limit(1).execute().data
        if n:
            supabase.table("notification_reads").upsert(
                {"notification_id": notification_id, "user_id": uid}, on_conflict="notification_id,user_id"
            ).execute()
    else:
        supabase.table("notifications").update({"is_read": True}).eq("id", notification_id).eq("org_id", org_id).eq("audience", _audience_of(current_user)).eq("recipient_id", uid).execute()
    return {"id": notification_id, "is_read": True}


@router.post("/read-all")
def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark all of the caller's notifications read (per-manager / per-parent)."""
    if not _enabled(current_user):
        return {"updated": True}
    org_id, uid = current_user["org_id"], current_user["id"]
    if _is_manager(current_user):
        ids = [
            r["id"]
            for r in supabase.table("notifications").select("id").eq("org_id", org_id).eq("audience", "manager")
            .order("created_at", desc=True).limit(_MGR_SCAN_LIMIT).execute().data
        ]
        read_ids = _manager_read_ids(uid, ids)
        missing = [i for i in ids if i not in read_ids]
        if missing:
            supabase.table("notification_reads").upsert(
                [{"notification_id": i, "user_id": uid} for i in missing], on_conflict="notification_id,user_id"
            ).execute()
    else:
        supabase.table("notifications").update({"is_read": True}).eq("org_id", org_id).eq("audience", _audience_of(current_user)).eq("recipient_id", uid).eq("is_read", False).execute()
    return {"updated": True}

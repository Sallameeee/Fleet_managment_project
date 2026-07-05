"""Persistent, nestable driver groups for the Full View (org-scoped).

Schema (see migrations/011): `driver_groups` is a self-referencing tree
(parent_group_id) and `driver_group_members` maps each driver to at most one
group (PK on driver_id). GET returns the tree with each group's drivers; the
mutation endpoints keep everything org-scoped and cycle-free.

Permissions: GET uses view_tracking (Full View viewers can see the tree);
mutations use manage_drivers (organizing the fleet is a driver-management act).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_permission
from database import supabase

router = APIRouter(prefix="/driver-groups", tags=["driver-groups"])


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1)
    parent_group_id: Optional[str] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    parent_group_id: Optional[str] = None  # provide (incl. null) to re-parent


class MemberAdd(BaseModel):
    driver_id: str = Field(..., min_length=1)


def _get_group(group_id: str, org_id: str) -> dict:
    res = (
        supabase.table("driver_groups")
        .select("*")
        .eq("id", group_id)
        .eq("org_id", org_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found.")
    return res.data[0]


def _all_groups(org_id: str) -> list:
    return (
        supabase.table("driver_groups")
        .select("id, name, parent_group_id, created_at")
        .eq("org_id", org_id)
        .order("name", desc=False)
        .execute()
        .data
    )


def _would_create_cycle(group_id: str, new_parent_id: Optional[str], groups: list) -> bool:
    """True if setting group_id's parent to new_parent_id makes a cycle — i.e.
    new_parent_id is group_id itself or any of its descendants. We detect it by
    climbing ANCESTORS of the proposed parent: if we reach group_id on the way
    up, the parent is actually below us, so the link would form a loop."""
    if new_parent_id is None:
        return False
    if new_parent_id == group_id:
        return True
    parent_of = {g["id"]: g["parent_group_id"] for g in groups}
    cur = new_parent_id
    seen = set()
    while cur is not None:
        if cur == group_id:
            return True
        if cur in seen:  # safety against pre-existing bad data
            break
        seen.add(cur)
        cur = parent_of.get(cur)
    return False


@router.get("")
def list_groups(current_user: dict = Depends(require_permission("view_tracking"))):
    """The org's groups as a nested tree, each with its member drivers."""
    org_id = current_user["org_id"]
    groups = _all_groups(org_id)

    members = (
        supabase.table("driver_group_members")
        .select("driver_id, group_id")
        .eq("org_id", org_id)
        .execute()
        .data
    )
    driver_ids = list({m["driver_id"] for m in members})
    names = {}
    if driver_ids:
        names = {
            p["id"]: p["name"]
            for p in supabase.table("profiles").select("id, name").in_("id", driver_ids).execute().data
        }
    drivers_by_group: dict = {}
    for m in members:
        drivers_by_group.setdefault(m["group_id"], []).append(
            {"driver_id": m["driver_id"], "name": names.get(m["driver_id"])}
        )

    # Assemble the tree.
    node = {
        g["id"]: {
            "id": g["id"],
            "name": g["name"],
            "parent_group_id": g["parent_group_id"],
            "drivers": sorted(drivers_by_group.get(g["id"], []), key=lambda d: (d["name"] or "").lower()),
            "children": [],
        }
        for g in groups
    }
    roots = []
    for g in groups:
        n = node[g["id"]]
        pid = g["parent_group_id"]
        if pid and pid in node:
            node[pid]["children"].append(n)
        else:
            roots.append(n)
    return {"groups": roots}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupCreate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    org_id = current_user["org_id"]
    if body.parent_group_id:
        _get_group(body.parent_group_id, org_id)  # parent must be ours
    row = (
        supabase.table("driver_groups")
        .insert({"org_id": org_id, "name": body.name, "parent_group_id": body.parent_group_id})
        .execute()
        .data[0]
    )
    return row


@router.patch("/{group_id}")
def update_group(
    group_id: str,
    body: GroupUpdate,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    org_id = current_user["org_id"]
    _get_group(group_id, org_id)
    update: dict = {}
    if body.name is not None:
        update["name"] = body.name
    # Re-parent only when the field was explicitly provided (null = make it a root).
    if "parent_group_id" in body.model_fields_set:
        new_parent = body.parent_group_id
        if new_parent:
            _get_group(new_parent, org_id)  # must be ours
        if _would_create_cycle(group_id, new_parent, _all_groups(org_id)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A group can't be moved inside itself or one of its own subgroups.",
            )
        update["parent_group_id"] = new_parent
    if not update:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update.")
    row = (
        supabase.table("driver_groups")
        .update(update)
        .eq("id", group_id)
        .eq("org_id", org_id)
        .execute()
        .data[0]
    )
    return row


@router.delete("/{group_id}", status_code=status.HTTP_200_OK)
def delete_group(
    group_id: str,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Delete a group; its child groups and member drivers MOVE UP one level
    (to this group's parent), or become ungrouped if it was a top-level group.
    Nothing is lost — only the one node disappears."""
    org_id = current_user["org_id"]
    group = _get_group(group_id, org_id)
    parent = group["parent_group_id"]

    # Child groups re-parent to this group's parent.
    supabase.table("driver_groups").update({"parent_group_id": parent}).eq(
        "parent_group_id", group_id
    ).eq("org_id", org_id).execute()

    # Member drivers move to the parent, or become ungrouped if there is none.
    if parent:
        supabase.table("driver_group_members").update({"group_id": parent}).eq(
            "group_id", group_id
        ).eq("org_id", org_id).execute()
    else:
        supabase.table("driver_group_members").delete().eq("group_id", group_id).eq(
            "org_id", org_id
        ).execute()

    supabase.table("driver_groups").delete().eq("id", group_id).eq("org_id", org_id).execute()
    return {"deleted": group_id, "reparented_to": parent}


@router.post("/{group_id}/members", status_code=status.HTTP_200_OK)
def add_member(
    group_id: str,
    body: MemberAdd,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Add (or MOVE) a driver into this group. Upsert on driver_id, so a driver
    ends up in exactly one group."""
    org_id = current_user["org_id"]
    _get_group(group_id, org_id)
    # Driver must be one of ours.
    drv = (
        supabase.table("profiles")
        .select("id")
        .eq("id", body.driver_id)
        .eq("org_id", org_id)
        .eq("role", "driver")
        .limit(1)
        .execute()
    )
    if not drv.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such driver in your organization.",
        )
    supabase.table("driver_group_members").upsert(
        {"driver_id": body.driver_id, "group_id": group_id, "org_id": org_id},
        on_conflict="driver_id",
    ).execute()
    return {"driver_id": body.driver_id, "group_id": group_id}


@router.delete("/{group_id}/members/{driver_id}", status_code=status.HTTP_200_OK)
def remove_member(
    group_id: str,
    driver_id: str,
    current_user: dict = Depends(require_permission("manage_drivers")),
):
    """Remove a driver from a group (back to ungrouped)."""
    org_id = current_user["org_id"]
    supabase.table("driver_group_members").delete().eq("driver_id", driver_id).eq(
        "group_id", group_id
    ).eq("org_id", org_id).execute()
    return {"removed": driver_id}

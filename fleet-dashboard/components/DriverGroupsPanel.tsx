"use client";

import { useRef, useState } from "react";
import {
  createDriverGroup,
  updateDriverGroup,
  deleteDriverGroup,
  addDriverToGroup,
  removeDriverFromGroup,
  type DriverGroup,
  type DriverPosition,
  type ManagerDriver,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";

type Drag = { kind: "driver" | "group"; id: string; fromGroupId: string | null } | null;

function collectDriverIds(g: DriverGroup): string[] {
  return [...g.drivers.map((d) => d.driver_id), ...g.children.flatMap(collectDriverIds)];
}
function countDrivers(g: DriverGroup): number {
  return g.drivers.length + g.children.reduce((n, c) => n + countDrivers(c), 0);
}

export default function DriverGroupsPanel({
  groups,
  roster,
  posById,
  selectedIds,
  focusedId,
  onToggleSelect,
  onSelectMany,
  onFocus,
  onReload,
}: {
  groups: DriverGroup[];
  roster: ManagerDriver[];
  posById: Record<string, DriverPosition>;
  selectedIds: string[];
  focusedId: string | null;
  onToggleSelect: (id: string) => void;
  onSelectMany: (ids: string[], on: boolean) => void;
  onFocus: (id: string) => void;
  onReload: () => void;
}) {
  const { t } = useT();
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [dragOver, setDragOver] = useState<string | null>(null);
  const dragRef = useRef<Drag>(null);

  // driver_id -> its current group id (for drag-out / row source)
  const driverGroupId: Record<string, string> = {};
  const walkMembership = (g: DriverGroup) => {
    g.drivers.forEach((d) => (driverGroupId[d.driver_id] = g.id));
    g.children.forEach(walkMembership);
  };
  groups.forEach(walkMembership);
  const groupedIds = new Set(Object.keys(driverGroupId));
  const ungrouped = roster.filter((d) => !groupedIds.has(d.id));

  function toggleCollapse(id: string) {
    setCollapsed((c) => {
      const n = new Set(c);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  async function newGroup(parentId: string | null) {
    const name = window.prompt(t("full.groupNamePrompt"));
    if (!name?.trim()) return;
    try {
      await createDriverGroup(name.trim(), parentId);
      toast.success(t("toast.created"));
      onReload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }
  async function rename(g: DriverGroup) {
    const name = window.prompt(t("full.groupNamePrompt"), g.name);
    if (!name?.trim() || name.trim() === g.name) return;
    try {
      await updateDriverGroup(g.id, { name: name.trim() });
      toast.success(t("toast.saved"));
      onReload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }
  async function del(g: DriverGroup) {
    if (!window.confirm(`${t("full.deleteGroup")} "${g.name}"?`)) return;
    try {
      await deleteDriverGroup(g.id);
      toast.success(t("toast.deleted"));
      onReload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  async function handleDrop(targetKind: "group" | "ungrouped", targetId: string | null) {
    const d = dragRef.current;
    dragRef.current = null;
    setDragOver(null);
    if (!d) return;
    try {
      if (d.kind === "driver") {
        if (targetKind === "group") {
          if (d.fromGroupId === targetId) return;
          await addDriverToGroup(targetId!, d.id);
        } else {
          if (!d.fromGroupId) return; // already ungrouped
          await removeDriverFromGroup(d.fromGroupId, d.id);
        }
      } else {
        if (d.id === targetId) return;
        await updateDriverGroup(d.id, { parent_group_id: targetKind === "group" ? targetId : null });
      }
      onReload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  function DriverRow({ d, fromGroupId, depth }: { d: { driver_id: string; name: string | null }; fromGroupId: string | null; depth: number }) {
    const online = posById[d.driver_id]?.online ?? false;
    const isFocused = d.driver_id === focusedId;
    const isSelected = selectedIds.includes(d.driver_id);
    return (
      <div
        draggable
        onDragStart={(e) => {
          dragRef.current = { kind: "driver", id: d.driver_id, fromGroupId };
          e.dataTransfer.setData("text/plain", d.driver_id);
          e.dataTransfer.effectAllowed = "move";
        }}
        style={{ paddingInlineStart: 8 + depth * 14 }}
        className={
          "flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm " +
          (isFocused ? "border-brand bg-brand/10" : isSelected ? "border-brand/40 bg-brand/5" : "border-transparent hover:bg-ink-800/60")
        }
      >
        <span className="cursor-grab text-slate-600" title="⋮⋮">⠿</span>
        <input type="checkbox" checked={isSelected} onChange={() => onToggleSelect(d.driver_id)} className="h-3.5 w-3.5 accent-[#3AA76D]" />
        <span className={"h-2 w-2 shrink-0 rounded-full " + (online ? "bg-green-500" : "bg-slate-500")} />
        <button onClick={() => onFocus(d.driver_id)} className="min-w-0 flex-1 truncate text-start text-slate-200">{d.name ?? "—"}</button>
      </div>
    );
  }

  function GroupNode({ g, depth }: { g: DriverGroup; depth: number }) {
    const open = !collapsed.has(g.id);
    const ids = collectDriverIds(g);
    const allSelected = ids.length > 0 && ids.every((id) => selectedIds.includes(id));
    const isOver = dragOver === g.id;
    return (
      <li>
        <div
          draggable
          onDragStart={(e) => {
            dragRef.current = { kind: "group", id: g.id, fromGroupId: null };
            e.dataTransfer.setData("text/plain", g.id);
            e.dataTransfer.effectAllowed = "move";
          }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(g.id); }}
          onDragLeave={() => setDragOver((o) => (o === g.id ? null : o))}
          onDrop={(e) => { e.preventDefault(); handleDrop("group", g.id); }}
          style={{ paddingInlineStart: 4 + depth * 14 }}
          className={"flex items-center gap-1.5 rounded-md border px-2 py-1.5 " + (isOver ? "border-brand bg-brand/15 ring-1 ring-brand" : "border-ink-800 bg-ink-900/60")}
        >
          <button onClick={() => toggleCollapse(g.id)} className="text-slate-400 hover:text-white" aria-label="toggle">
            {open ? "▾" : "▸"}
          </button>
          <input
            type="checkbox"
            checked={allSelected}
            onChange={() => onSelectMany(ids, !allSelected)}
            className="h-3.5 w-3.5 accent-[#3AA76D]"
            title={t("full.selectGroup")}
          />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-white">{g.name}</span>
          <span className="shrink-0 text-[11px] text-slate-500">{countDrivers(g)}</span>
          <div className="flex shrink-0 items-center gap-0.5">
            <button onClick={() => newGroup(g.id)} title={t("full.newSubgroup")} className="rounded p-1 text-slate-400 hover:text-white">＋</button>
            <button onClick={() => rename(g)} title={t("full.rename")} className="rounded p-1 text-slate-400 hover:text-white">✎</button>
            <button onClick={() => del(g)} title={t("full.deleteGroup")} className="rounded p-1 text-red-300 hover:bg-red-500/10">✕</button>
          </div>
        </div>

        {open && (
          <div className="mt-0.5 space-y-0.5">
            {g.drivers.length === 0 && g.children.length === 0 && (
              <div style={{ paddingInlineStart: 8 + (depth + 1) * 14 }} className="py-1 text-[11px] italic text-slate-600">{t("full.emptyGroup")}</div>
            )}
            {g.drivers.map((d) => (
              <DriverRow key={d.driver_id} d={d} fromGroupId={g.id} depth={depth + 1} />
            ))}
            <ul className="space-y-0.5">
              {g.children.map((c) => (
                <GroupNode key={c.id} g={c} depth={depth + 1} />
              ))}
            </ul>
          </div>
        )}
      </li>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-slate-500">{t("full.dragHint")}</p>
        <button onClick={() => newGroup(null)} className="rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-slate-200 hover:border-brand hover:text-white">+ {t("full.newGroup")}</button>
      </div>

      {groups.length === 0 &&<div className="rounded-lg border border-dashed border-ink-700 px-3 py-4 text-center text-xs text-slate-500">{t("full.noGroups")}</div>}

      <ul className="max-h-[420px] space-y-1 overflow-y-auto">
        {groups.map((g) => (
          <GroupNode key={g.id} g={g} depth={0} />
        ))}
      </ul>

      {/* Ungrouped — droppable target to remove a driver from a group */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver("ungrouped"); }}
        onDragLeave={() => setDragOver((o) => (o === "ungrouped" ? null : o))}
        onDrop={(e) => { e.preventDefault(); handleDrop("ungrouped", null); }}
        className={"rounded-lg border p-2 " + (dragOver === "ungrouped" ? "border-brand bg-brand/15 ring-1 ring-brand" : "border-ink-800")}
      >
        <div className="mb-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("full.ungrouped")} ({ungrouped.length})</div>
        <div className="max-h-40 space-y-0.5 overflow-y-auto">
          {ungrouped.map((d) => (
            <DriverRow key={d.id} d={{ driver_id: d.id, name: d.name }} fromGroupId={null} depth={0} />
          ))}
        </div>
      </div>
    </div>
  );
}

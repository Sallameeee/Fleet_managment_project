"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listAssignments,
  createAssignment,
  updateAssignment,
  deleteAssignment,
  listDrivers,
  listRoutes,
  listVehicles,
  listBusDrivers,
  AssignmentConflictError,
  type ManagerAssignment,
  type ManagerDriver,
  type ManagerRoute,
  type ManagerVehicle,
  type BusDriver,
  type AssignmentConflict,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";

const emptyForm = { driver_id: "", route_id: "", vehicle_id: "", trip_date: "", shift_label: "", start_time: "", end_time: "", bus_driver_id: "" };

// Fill "{name}"-style placeholders in a translated template.
function fill(template: string, vars: Record<string, string>): string {
  return Object.entries(vars).reduce((acc, [k, v]) => acc.split(`{${k}}`).join(v), template);
}

export default function ManagerAssignmentsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();
  const [assignments, setAssignments] = useState<ManagerAssignment[]>([]);
  const [dateFilter, setDateFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [busDrivers, setBusDrivers] = useState<BusDriver[]>([]);

  const [open, setOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAssignments(await listAssignments(dateFilter || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dateFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function loadOptions() {
    try {
      const [d, r, v] = await Promise.all([listDrivers(), listRoutes(), listVehicles()]);
      setDrivers(d);
      setRoutes(r);
      setVehicles(v);
      if (isSchool) setBusDrivers(await listBusDrivers()); // school-only bus driver options
    } catch {
      /* the selects just stay empty */
    }
  }

  async function openCreate() {
    setEditingId(null);
    setForm(emptyForm);
    setFormError(null);
    setOpen(true);
    await loadOptions();
  }

  async function openEdit(a: ManagerAssignment) {
    setEditingId(a.id);
    setForm({
      driver_id: a.driver_id,
      route_id: a.route_id,
      vehicle_id: a.vehicle_id,
      trip_date: a.trip_date,
      shift_label: a.shift_label ?? "",
      start_time: (a.start_time ?? "").slice(0, 5),
      end_time: (a.end_time ?? "").slice(0, 5),
      bus_driver_id: a.bus_driver_id ?? "",
    });
    setFormError(null);
    setOpen(true);
    await loadOptions();
  }

  function conflictMessage(c: AssignmentConflict): string {
    const vars = {
      name: c.name ?? "—",
      route: c.route_name ?? "—",
      start: c.start,
      end: c.end,
    };
    return fill(t(c.resource === "driver" ? "assign.conflictDriver" : "assign.conflictVehicle"), vars);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Client-side window sanity check (server enforces it too).
    if (form.start_time && form.end_time && form.end_time <= form.start_time) {
      setFormError(t("assign.endAfterStart"));
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const payload = {
        driver_id: form.driver_id,
        route_id: form.route_id,
        vehicle_id: form.vehicle_id,
        trip_date: form.trip_date,
        shift_label: form.shift_label.trim() || undefined,
        start_time: form.start_time || undefined,
        end_time: form.end_time || undefined,
        // School module: the linked bus driver (only sent for school orgs).
        bus_driver_id: isSchool ? form.bus_driver_id || undefined : undefined,
      };
      if (editingId) await updateAssignment(editingId, payload);
      else await createAssignment(payload);
      setOpen(false);
      toast.success(editingId ? t("toast.saved") : t("toast.created"));
      await load();
    } catch (err) {
      if (err instanceof AssignmentConflictError) setFormError(conflictMessage(err.conflict));
      else setFormError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(a: ManagerAssignment) {
    if (!window.confirm(t("assign.deleteConfirm"))) return;
    try {
      await deleteAssignment(a.id);
      toast.success(t("toast.deleted"));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  const canSubmit =
    !!form.driver_id && !!form.route_id && !!form.vehicle_id && !!form.trip_date && !!form.start_time && !!form.end_time;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.assignments")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${assignments.length}`}</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value)}
            className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none"
          />
          {dateFilter && (
            <button onClick={() => setDateFilter("")} className="text-sm text-slate-400 hover:text-white">{t("assign.clear")}</button>
          )}
          <button onClick={openCreate} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">
            + {t("assign.newAssignment")}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.date")}</th>
              <th className="px-4 py-3">{isSchool ? t("common.supervisor") : t("common.driver")}</th>
              <th className="px-4 py-3">{t("common.route")}</th>
              <th className="px-4 py-3">{t("common.vehicle")}</th>
              {isSchool && <th className="px-4 py-3">{t("common.busDriver")}</th>}
              <th className="px-4 py-3">{t("assign.shift")}</th>
              <th className="px-4 py-3">{t("assign.start")}</th>
              <th className="px-4 py-3">{t("assign.end")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && assignments.length === 0 && !error && (
              <tr><td colSpan={isSchool ? 9 : 8} className="px-4 py-8 text-center text-slate-500">{t("assign.none")}</td></tr>
            )}
            {assignments.map((a) => (
              <tr key={a.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 text-slate-200">{a.trip_date}</td>
                <td className="px-4 py-3 text-white">{a.driver_name ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{a.route_name ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{a.vehicle_bus_number ?? "—"}</td>
                {isSchool && <td className="px-4 py-3 text-slate-300">{a.bus_driver_name ?? "—"}</td>}
                <td className="px-4 py-3 text-slate-400">{a.shift_label ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{a.start_time ? a.start_time.slice(0, 5) : "—"}</td>
                <td className="px-4 py-3 text-slate-400">{a.end_time ? a.end_time.slice(0, 5) : "—"}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => openEdit(a)}
                      title={t("common.edit")}
                      aria-label={t("common.edit")}
                      className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white"
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" /></svg>
                    </button>
                    <button
                      onClick={() => handleDelete(a)}
                      title={t("common.delete")}
                      aria-label={t("common.delete")}
                      className="rounded-md border border-red-500/40 p-1.5 text-red-300 hover:bg-red-500/10"
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={editingId ? t("assign.editAssignment") : t("assign.newAssignment")}>
        <form onSubmit={handleSubmit} className="space-y-3">
          <Select label={`${isSchool ? t("common.supervisor") : t("common.driver")} *`} value={form.driver_id} onChange={(v) => setForm((f) => ({ ...f, driver_id: v }))} options={drivers.map((d) => ({ value: d.id, label: `${d.name} (${d.username})` }))} placeholder={t("assign.select")} />
          <Select label={`${t("common.route")} *`} value={form.route_id} onChange={(v) => setForm((f) => ({ ...f, route_id: v }))} options={routes.map((r) => ({ value: r.id, label: r.name }))} placeholder={t("assign.select")} />
          <Select label={`${t("common.vehicle")} *`} value={form.vehicle_id} onChange={(v) => setForm((f) => ({ ...f, vehicle_id: v }))} options={vehicles.map((v) => ({ value: v.id, label: v.bus_number }))} placeholder={t("assign.select")} />
          <Input label={`${t("assign.tripDate")} *`} type="date" value={form.trip_date} onChange={(e) => setForm((f) => ({ ...f, trip_date: e.target.value }))} required />
          <div className="grid grid-cols-2 gap-3">
            <Input label={`${t("assign.startTime")} *`} type="time" value={form.start_time} onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))} required />
            <Input label={`${t("assign.endTime")} *`} type="time" value={form.end_time} onChange={(e) => setForm((f) => ({ ...f, end_time: e.target.value }))} required />
          </div>
          <Input label={t("assign.shiftLabel")} value={form.shift_label} onChange={(e) => setForm((f) => ({ ...f, shift_label: e.target.value }))} />
          {isSchool && (
            <Select
              label={t("assign.busDriver")}
              value={form.bus_driver_id}
              onChange={(v) => setForm((f) => ({ ...f, bus_driver_id: v }))}
              options={busDrivers.map((b) => ({ value: b.id, label: b.phone ? `${b.name} · ${b.phone}` : b.name }))}
              placeholder={t("assign.select")}
            />
          )}
          {formError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{formError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={saving} className="w-auto px-6" disabled={!canSubmit}>{editingId ? t("assign.save") : t("assign.assign")}</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-300">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40"
      >
        <option value="">{placeholder ?? "Select…"}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </label>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listAssignments,
  createAssignment,
  listDrivers,
  listRoutes,
  listVehicles,
  type ManagerAssignment,
  type ManagerDriver,
  type ManagerRoute,
  type ManagerVehicle,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";

export default function ManagerAssignmentsPage() {
  const { t } = useT();
  const [assignments, setAssignments] = useState<ManagerAssignment[]>([]);
  const [dateFilter, setDateFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ driver_id: "", route_id: "", vehicle_id: "", trip_date: "", shift_label: "", start_time: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

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
  }, [dateFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function openModal() {
    setForm({ driver_id: "", route_id: "", vehicle_id: "", trip_date: "", shift_label: "", start_time: "" });
    setCreateError(null);
    setOpen(true);
    // Populate dropdowns from the org's existing records.
    try {
      const [d, r, v] = await Promise.all([listDrivers(), listRoutes(), listVehicles()]);
      setDrivers(d);
      setRoutes(r);
      setVehicles(v);
    } catch {
      /* the selects just stay empty; user sees no options */
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createAssignment({
        driver_id: form.driver_id,
        route_id: form.route_id,
        vehicle_id: form.vehicle_id,
        trip_date: form.trip_date,
        shift_label: form.shift_label.trim() || undefined,
        start_time: form.start_time || undefined,
      });
      setOpen(false);
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

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
          <button onClick={openModal} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">
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
              <th className="px-4 py-3">{t("common.driver")}</th>
              <th className="px-4 py-3">{t("common.route")}</th>
              <th className="px-4 py-3">{t("common.vehicle")}</th>
              <th className="px-4 py-3">{t("assign.shift")}</th>
              <th className="px-4 py-3">{t("assign.start")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && assignments.length === 0 && !error && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">{t("assign.none")}</td></tr>
            )}
            {assignments.map((a) => (
              <tr key={a.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 text-slate-200">{a.trip_date}</td>
                <td className="px-4 py-3 text-white">{a.driver_name ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{a.route_name ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{a.vehicle_bus_number ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{a.shift_label ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{a.start_time ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={t("assign.newAssignment")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Select label={`${t("common.driver")} *`} value={form.driver_id} onChange={(v) => setForm((f) => ({ ...f, driver_id: v }))} options={drivers.map((d) => ({ value: d.id, label: `${d.name} (${d.username})` }))} placeholder={t("assign.select")} />
          <Select label={`${t("common.route")} *`} value={form.route_id} onChange={(v) => setForm((f) => ({ ...f, route_id: v }))} options={routes.map((r) => ({ value: r.id, label: r.name }))} placeholder={t("assign.select")} />
          <Select label={`${t("common.vehicle")} *`} value={form.vehicle_id} onChange={(v) => setForm((f) => ({ ...f, vehicle_id: v }))} options={vehicles.map((v) => ({ value: v.id, label: v.bus_number }))} placeholder={t("assign.select")} />
          <div className="grid grid-cols-2 gap-3">
            <Input label={`${t("assign.tripDate")} *`} type="date" value={form.trip_date} onChange={(e) => setForm((f) => ({ ...f, trip_date: e.target.value }))} required />
            <Input label={t("assign.startTime")} type="time" value={form.start_time} onChange={(e) => setForm((f) => ({ ...f, start_time: e.target.value }))} />
          </div>
          <Input label={t("assign.shiftLabel")} value={form.shift_label} onChange={(e) => setForm((f) => ({ ...f, shift_label: e.target.value }))} />
          {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={creating} className="w-auto px-6" disabled={!form.driver_id || !form.route_id || !form.vehicle_id || !form.trip_date}>{t("assign.assign")}</Button>
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

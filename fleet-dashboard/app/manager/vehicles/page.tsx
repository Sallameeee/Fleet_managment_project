"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listVehicles,
  createVehicle,
  updateVehicle,
  deleteVehicle,
  bulkCreateVehicles,
  trackingUrl,
  type ManagerVehicle,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import BulkImport from "@/components/BulkImport";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";
import { EditIcon, TrashIcon } from "@/components/RowIcons";

const VEHICLE_COLUMNS = [
  { key: "bus_number", header: "bus_number", aliases: ["bus number", "bus"] },
  { key: "plate_number", header: "plate_number", aliases: ["plate", "plate number"] },
  { key: "capacity", header: "capacity", aliases: ["seats"] },
];
const VEHICLE_SAMPLE = [
  { bus_number: "BUS-101", plate_number: "ABC-1234", capacity: "40" },
  { bus_number: "BUS-102", plate_number: "XYZ-5678", capacity: "52" },
];

export default function ManagerVehiclesPage() {
  const { t } = useT();
  const toast = useToast();
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ bus_number: "", plate_number: "", capacity: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const [editVehicle, setEditVehicle] = useState<ManagerVehicle | null>(null);
  const [eForm, setEForm] = useState({ bus_number: "", plate_number: "", capacity: "", is_active: true });
  const [saving, setSaving] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setVehicles(await listVehicles());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openModal() {
    setForm({ bus_number: "", plate_number: "", capacity: "" });
    setCreateError(null);
    setOpen(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createVehicle({
        bus_number: form.bus_number.trim(),
        plate_number: form.plate_number.trim() || undefined,
        capacity: form.capacity.trim() ? Number(form.capacity) : undefined,
      });
      setOpen(false);
      toast.success(t("toast.created"));
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  function openEdit(v: ManagerVehicle) {
    setEditVehicle(v);
    setEForm({ bus_number: v.bus_number, plate_number: v.plate_number ?? "", capacity: v.capacity != null ? String(v.capacity) : "", is_active: v.is_active });
    setEError(null);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editVehicle) return;
    setSaving(true);
    setEError(null);
    try {
      await updateVehicle(editVehicle.id, { bus_number: eForm.bus_number.trim(), plate_number: eForm.plate_number.trim() || null, capacity: eForm.capacity.trim() ? Number(eForm.capacity) : null, is_active: eForm.is_active });
      setEditVehicle(null);
      toast.success(t("toast.saved"));
      await load();
    } catch (err) {
      setEError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(v: ManagerVehicle) {
    if (!window.confirm(t("vehicles.deleteConfirm"))) return;
    try {
      await deleteVehicle(v.id);
      toast.success(t("toast.deleted"));
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.failed"));
    }
  }

  async function copyLink(id: string, url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(id);
      setTimeout(() => setCopied((c) => (c === id ? null : c)), 1500);
    } catch {
      /* clipboard blocked; the link is still visible to copy manually */
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.vehicles")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${vehicles.length}`}</p>
        </div>
        <div className="flex items-center gap-2">
          <BulkImport templateName="vehicles_template.csv" columns={VEHICLE_COLUMNS} sample={VEHICLE_SAMPLE} onImport={bulkCreateVehicles} onDone={load} />
          <button
            onClick={openModal}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
          >
            + {t("vehicles.newVehicle")}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      <div className="overflow-x-auto rounded-xl border border-ink-800">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("vehicles.busNumber")}</th>
              <th className="px-4 py-3">{t("vehicles.plate")}</th>
              <th className="px-4 py-3">{t("vehicles.capacity")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3">{t("vehicles.trackingLink")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && vehicles.length === 0 && !error && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">{t("common.none")}</td></tr>
            )}
            {vehicles.map((v) => {
              const url = trackingUrl(v.share_token);
              return (
                <tr key={v.id} className="hover:bg-ink-900/40">
                  <td className="px-4 py-3 font-medium text-slate-200">{v.bus_number}</td>
                  <td className="px-4 py-3 text-slate-400">{v.plate_number ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-400">{v.capacity ?? "—"}</td>
                  <td className="px-4 py-3"><StatusBadge status={v.is_active ? "active" : "inactive"} /></td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <a
                        href={url}
                        target="_blank"
                        rel="noreferrer"
                        className="max-w-[280px] truncate font-mono text-xs text-brand-sage hover:underline"
                        title={url}
                      >
                        {url}
                      </a>
                      <button
                        onClick={() => copyLink(v.id, url)}
                        className="shrink-0 rounded-md border border-ink-700 px-2 py-0.5 text-xs text-slate-300 hover:border-brand hover:text-white"
                      >
                        {copied === v.id ? t("vehicles.copied") : t("vehicles.copy")}
                      </button>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1.5">
                      <button onClick={() => openEdit(v)} title={t("vehicles.editVehicle")} aria-label={t("vehicles.editVehicle")} className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white"><EditIcon /></button>
                      <button onClick={() => handleDelete(v)} title={t("common.delete")} aria-label={t("common.delete")} className="rounded-md border border-red-500/40 p-1.5 text-red-300 hover:bg-red-500/10"><TrashIcon /></button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={t("vehicles.newVehicle")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Input label={`${t("vehicles.busNumber")} *`} value={form.bus_number} onChange={(e) => setForm((f) => ({ ...f, bus_number: e.target.value }))} required />
          <Input label={t("vehicles.plateNumber")} value={form.plate_number} onChange={(e) => setForm((f) => ({ ...f, plate_number: e.target.value }))} />
          <Input label={t("vehicles.capacity")} type="number" min={0} value={form.capacity} onChange={(e) => setForm((f) => ({ ...f, capacity: e.target.value }))} />
          {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
          </div>
        </form>
      </Modal>

      <Modal open={editVehicle !== null} onClose={() => setEditVehicle(null)} title={t("vehicles.editVehicle")}>
        {editVehicle && (
          <form onSubmit={handleEdit} className="space-y-3">
            <Input label={`${t("vehicles.busNumber")} *`} value={eForm.bus_number} onChange={(e) => setEForm((f) => ({ ...f, bus_number: e.target.value }))} required />
            <Input label={t("vehicles.plateNumber")} value={eForm.plate_number} onChange={(e) => setEForm((f) => ({ ...f, plate_number: e.target.value }))} />
            <Input label={t("vehicles.capacity")} type="number" min={0} value={eForm.capacity} onChange={(e) => setEForm((f) => ({ ...f, capacity: e.target.value }))} />
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={eForm.is_active} onChange={(e) => setEForm((f) => ({ ...f, is_active: e.target.checked }))} className="h-4 w-4 accent-[#3AA76D]" />
              {t("common.active")}
            </label>
            {eError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{eError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setEditVehicle(null)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

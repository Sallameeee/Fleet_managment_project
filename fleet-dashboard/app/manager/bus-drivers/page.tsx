"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listBusDrivers,
  createBusDriver,
  updateBusDriver,
  deleteBusDriver,
  bulkCreateBusDrivers,
  type BusDriver,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import BulkImport from "@/components/BulkImport";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import { EditIcon, TrashIcon } from "@/components/RowIcons";

const BUS_DRIVER_COLUMNS = [
  { key: "name", header: "name" },
  { key: "phone", header: "phone" },
  { key: "license_number", header: "license_number", aliases: ["license", "license no"] },
  { key: "license_start_date", header: "license_start_date", aliases: ["license start", "start_date"] },
  { key: "license_end_date", header: "license_end_date", aliases: ["license end", "end_date", "expiry"] },
];
const BUS_DRIVER_SAMPLE = [
  { name: "Mahmoud Salah", phone: "0100-111-2222", license_number: "DL-55123", license_start_date: "2023-01-15", license_end_date: "2028-01-14" },
  { name: "Osama Fathy", phone: "0101-333-4444", license_number: "DL-55987", license_start_date: "2022-06-01", license_end_date: "2027-05-31" },
];

const EMPTY = { name: "", phone: "", license_number: "", license_start_date: "", license_end_date: "" };

function isExpired(endDate: string | null): boolean {
  if (!endDate) return false;
  const d = new Date(endDate);
  if (Number.isNaN(d.getTime())) return false;
  // Compare on date only (end of that day still valid).
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return d < today;
}

export default function ManagerBusDriversPage() {
  const { t } = useT();
  const toast = useToast();
  const [rows, setRows] = useState<BusDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<BusDriver | null>(null);
  const [form, setForm] = useState({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listBusDrivers());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    setForm({ ...EMPTY });
    setFormError(null);
    setOpen(true);
  }

  function openEdit(b: BusDriver) {
    setEditing(b);
    setForm({
      name: b.name,
      phone: b.phone ?? "",
      license_number: b.license_number ?? "",
      license_start_date: (b.license_start_date ?? "").slice(0, 10),
      license_end_date: (b.license_end_date ?? "").slice(0, 10),
    });
    setFormError(null);
    setOpen(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setFormError(null);
    try {
      const payload = {
        name: form.name.trim(),
        phone: form.phone.trim() || null,
        license_number: form.license_number.trim() || null,
        license_start_date: form.license_start_date || null,
        license_end_date: form.license_end_date || null,
      };
      if (editing) await updateBusDriver(editing.id, payload);
      else await createBusDriver(payload);
      setOpen(false);
      toast.success(editing ? t("toast.saved") : t("toast.created"));
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(b: BusDriver) {
    if (!window.confirm(t("busDrivers.deleteConfirm"))) return;
    try {
      await deleteBusDriver(b.id);
      toast.success(t("toast.deleted"));
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.failed"));
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.busDrivers")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("busDrivers.subtitle")}</p>
        </div>
        <div className="flex items-center gap-2">
          <BulkImport templateName="bus_drivers_template.csv" columns={BUS_DRIVER_COLUMNS} sample={BUS_DRIVER_SAMPLE} onImport={bulkCreateBusDrivers} onDone={load} />
          <button
            onClick={openCreate}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
          >
            + {t("busDrivers.new")}
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
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("common.phone")}</th>
              <th className="px-4 py-3">{t("drivers.license")}</th>
              <th className="px-4 py-3">{t("busDrivers.licenseValidity")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && rows.length === 0 && !error && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">—</td></tr>
            )}
            {rows.map((b) => {
              const expired = isExpired(b.license_end_date);
              return (
                <tr key={b.id} className="hover:bg-ink-900/40">
                  <td className="px-4 py-3 font-medium text-white">{b.name}</td>
                  <td className="px-4 py-3 text-slate-300">{b.phone ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-400">{b.license_number ?? "—"}</td>
                  <td className="px-4 py-3">
                    {b.license_start_date || b.license_end_date ? (
                      <span className="inline-flex items-center gap-2">
                        <span className="text-slate-300">
                          {(b.license_start_date ?? "—").slice(0, 10)} → {(b.license_end_date ?? "—").slice(0, 10)}
                        </span>
                        {expired && (
                          <span className="rounded-full border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-300">
                            {t("busDrivers.expired")}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-slate-500">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1.5">
                      <button onClick={() => openEdit(b)} title={t("busDrivers.edit")} aria-label={t("busDrivers.edit")} className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white"><EditIcon /></button>
                      <button onClick={() => handleDelete(b)} title={t("common.delete")} aria-label={t("common.delete")} className="rounded-md border border-red-500/40 p-1.5 text-red-300 hover:bg-red-500/10"><TrashIcon /></button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={editing ? t("busDrivers.edit") : t("busDrivers.new")}>
        <form onSubmit={handleSave} className="space-y-3">
          <Input label={`${t("common.name")} *`} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          <Input label={t("common.phone")} value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
          <Input label={t("drivers.license")} value={form.license_number} onChange={(e) => setForm((f) => ({ ...f, license_number: e.target.value }))} />
          <div className="grid grid-cols-2 gap-3">
            <Input label={t("drivers.licenseStart")} type="date" value={form.license_start_date} onChange={(e) => setForm((f) => ({ ...f, license_start_date: e.target.value }))} />
            <Input label={t("drivers.licenseExpiry")} type="date" value={form.license_end_date} onChange={(e) => setForm((f) => ({ ...f, license_end_date: e.target.value }))} />
          </div>
          {formError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{formError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={saving} className="w-auto px-6">{editing ? t("common.save") : t("common.create")}</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

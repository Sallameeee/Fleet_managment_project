"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listDrivers,
  createDriver,
  updateDriver,
  deleteDriver,
  getManagerSlug,
  type ManagerDriver,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";
import { EditIcon, TrashIcon } from "@/components/RowIcons";

const EMPTY = { name: "", username: "", password: "", phone: "", email: "", license_number: "", license_start_date: "", license_expiry_date: "" };

export default function ManagerDriversPage() {
  const { t } = useT();
  const toast = useToast();
  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdLogin, setCreatedLogin] = useState<string | null>(null);

  const [editDriver, setEditDriver] = useState<ManagerDriver | null>(null);
  const [eForm, setEForm] = useState({ name: "", phone: "", is_active: true, license_number: "", license_start_date: "", license_expiry_date: "" });
  const [saving, setSaving] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDrivers(await listDrivers());
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
    setForm({ ...EMPTY });
    setCreateError(null);
    setCreatedLogin(null);
    setOpen(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      const res = await createDriver({
        name: form.name.trim(),
        username: form.username.trim(),
        password: form.password,
        phone: form.phone.trim() || undefined,
        email: form.email.trim() || undefined,
        license_number: form.license_number.trim() || undefined,
        license_start_date: form.license_start_date || undefined,
        license_expiry_date: form.license_expiry_date || undefined,
      });
      const slug = getManagerSlug();
      setCreatedLogin(slug ? `${res.username}@${slug}` : res.username);
      toast.success(t("toast.created"));
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  function openEdit(d: ManagerDriver) {
    setEditDriver(d);
    setEForm({
      name: d.name,
      phone: d.phone ?? "",
      is_active: d.is_active,
      license_number: d.license_number ?? "",
      license_start_date: (d.license_start_date ?? "").slice(0, 10),
      license_expiry_date: (d.license_expiry_date ?? "").slice(0, 10),
    });
    setEError(null);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editDriver) return;
    setSaving(true);
    setEError(null);
    try {
      await updateDriver(editDriver.id, {
        name: eForm.name.trim(),
        phone: eForm.phone.trim() || null,
        is_active: eForm.is_active,
        license_number: eForm.license_number.trim() || null,
        license_start_date: eForm.license_start_date || null,
        license_expiry_date: eForm.license_expiry_date || null,
      });
      setEditDriver(null);
      toast.success(t("toast.saved"));
      await load();
    } catch (err) {
      setEError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(d: ManagerDriver) {
    if (!window.confirm(t("drivers.deleteConfirm"))) return;
    try {
      await deleteDriver(d.id);
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
          <h1 className="text-2xl font-semibold text-white">{t("nav.drivers")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${drivers.length}`}</p>
        </div>
        <button
          onClick={openModal}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
        >
          + {t("drivers.newDriver")}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.driver")}</th>
              <th className="px-4 py-3">{t("common.username")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3">{t("summary.online")}</th>
              <th className="px-4 py-3">{t("drivers.currentVehicle")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && drivers.length === 0 && !error && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">—</td></tr>
            )}
            {drivers.map((d) => (
              <tr key={d.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 font-medium text-white">{d.name}</td>
                <td className="px-4 py-3 text-slate-400">{d.username}</td>
                <td className="px-4 py-3"><StatusBadge status={d.is_active ? "active" : "inactive"} /></td>
                <td className="px-4 py-3">
                  {d.online ? (
                    <span className="inline-flex items-center gap-1.5 text-brand-sage">
                      <span className="h-2 w-2 rounded-full bg-brand-sage" />{t("common.online")}
                    </span>
                  ) : (
                    <span className="text-slate-500">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-300">{d.current_vehicle ?? "—"}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <button onClick={() => openEdit(d)} title={t("drivers.editDriver")} aria-label={t("drivers.editDriver")} className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white"><EditIcon /></button>
                    <button onClick={() => handleDelete(d)} title={t("common.delete")} aria-label={t("common.delete")} className="rounded-md border border-red-500/40 p-1.5 text-red-300 hover:bg-red-500/10"><TrashIcon /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={t("drivers.newDriver")}>
        {createdLogin ? (
          <div className="space-y-4">
            <div className="rounded-lg border border-brand/30 bg-brand/10 px-4 py-3 text-sm text-brand-sage">
              {t("drivers.appLogin")}
              <div className="mt-2 select-all font-mono text-base text-white">{createdLogin}</div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={openModal} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("drivers.addAnother")}</button>
              <Button type="button" onClick={() => setOpen(false)} className="w-auto px-4">{t("common.done")}</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Input label={`${t("common.name")} *`} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <Input label={`${t("common.username")} *`} value={form.username} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} required />
            </div>
            <Input label={`${t("common.password")} *`} type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} required minLength={6} />
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("common.phone")} value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
              <Input label={t("common.email")} type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
            </div>
            <Input label={t("drivers.license")} value={form.license_number} onChange={(e) => setForm((f) => ({ ...f, license_number: e.target.value }))} />
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("drivers.licenseStart")} type="date" value={form.license_start_date} onChange={(e) => setForm((f) => ({ ...f, license_start_date: e.target.value }))} />
              <Input label={t("drivers.licenseExpiry")} type="date" value={form.license_expiry_date} onChange={(e) => setForm((f) => ({ ...f, license_expiry_date: e.target.value }))} />
            </div>
            {createError &&<div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
            </div>
          </form>
        )}
      </Modal>

      <Modal open={editDriver !== null} onClose={() => setEditDriver(null)} title={t("drivers.editDriver")}>
        {editDriver && (
          <form onSubmit={handleEdit} className="space-y-3">
            <Input label={`${t("common.name")} *`} value={eForm.name} onChange={(e) => setEForm((f) => ({ ...f, name: e.target.value }))} required />
            <Input label={t("common.phone")} value={eForm.phone} onChange={(e) => setEForm((f) => ({ ...f, phone: e.target.value }))} />
            <Input label={t("drivers.license")} value={eForm.license_number} onChange={(e) => setEForm((f) => ({ ...f, license_number: e.target.value }))} />
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("drivers.licenseStart")} type="date" value={eForm.license_start_date} onChange={(e) => setEForm((f) => ({ ...f, license_start_date: e.target.value }))} />
              <Input label={t("drivers.licenseExpiry")} type="date" value={eForm.license_expiry_date} onChange={(e) => setEForm((f) => ({ ...f, license_expiry_date: e.target.value }))} />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={eForm.is_active} onChange={(e) => setEForm((f) => ({ ...f, is_active: e.target.checked }))} className="h-4 w-4 accent-[#3AA76D]" />
              {t("common.active")}
            </label>
            {eError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{eError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setEditDriver(null)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

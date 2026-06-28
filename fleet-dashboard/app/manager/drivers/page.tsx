"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listDrivers,
  createDriver,
  getManagerSlug,
  type ManagerDriver,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";

const EMPTY = { name: "", username: "", password: "", phone: "", email: "" };

export default function ManagerDriversPage() {
  const { t } = useT();
  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdLogin, setCreatedLogin] = useState<string | null>(null);

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
      });
      const slug = getManagerSlug();
      setCreatedLogin(slug ? `${res.username}@${slug}` : res.username);
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
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && drivers.length === 0 && !error && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">—</td></tr>
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
            {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

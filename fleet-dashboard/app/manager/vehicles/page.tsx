"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listVehicles,
  createVehicle,
  trackingUrl,
  type ManagerVehicle,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";

export default function ManagerVehiclesPage() {
  const { t } = useT();
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ bus_number: "", plate_number: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

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
    setForm({ bus_number: "", plate_number: "" });
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
      });
      setOpen(false);
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
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
        <button
          onClick={openModal}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
        >
          + {t("vehicles.newVehicle")}
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
              <th className="px-4 py-3">{t("vehicles.busNumber")}</th>
              <th className="px-4 py-3">{t("vehicles.plate")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3">{t("vehicles.trackingLink")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && vehicles.length === 0 && !error && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-slate-500">{t("common.none")}</td></tr>
            )}
            {vehicles.map((v) => {
              const url = trackingUrl(v.share_token);
              return (
                <tr key={v.id} className="hover:bg-ink-900/40">
                  <td className="px-4 py-3 font-medium text-slate-200">{v.bus_number}</td>
                  <td className="px-4 py-3 text-slate-400">{v.plate_number ?? "—"}</td>
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
          {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listRoutes,
  createRoute,
  type ManagerRoute,
  type RouteStop,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";

function blankStop(order: number): RouteStop {
  return { name: "", lat: 0, lng: 0, stop_order: order, dwell_minutes: 0 };
}

export default function ManagerRoutesPage() {
  const { t } = useT();
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewRoute, setViewRoute] = useState<ManagerRoute | null>(null);

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [totalKm, setTotalKm] = useState("");
  const [estMin, setEstMin] = useState("");
  const [stops, setStops] = useState<RouteStop[]>([blankStop(1)]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRoutes(await listRoutes());
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
    setName("");
    setTotalKm("");
    setEstMin("");
    setStops([blankStop(1)]);
    setCreateError(null);
    setOpen(true);
  }

  function updateStop(i: number, field: keyof RouteStop, value: string) {
    setStops((s) =>
      s.map((st, idx) =>
        idx === i
          ? { ...st, [field]: field === "name" ? value : Number(value) }
          : st,
      ),
    );
  }

  function addStop() {
    setStops((s) => [...s, blankStop(s.length + 1)]);
  }

  function removeStop(i: number) {
    setStops((s) => s.filter((_, idx) => idx !== i).map((st, idx) => ({ ...st, stop_order: idx + 1 })));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await createRoute({
        name: name.trim(),
        total_km: totalKm ? Number(totalKm) : undefined,
        est_minutes: estMin ? Number(estMin) : undefined,
        stops: stops.filter((s) => s.name.trim()),
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
          <h1 className="text-2xl font-semibold text-white">{t("nav.routes")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${routes.length}`}</p>
        </div>
        <button onClick={openModal} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">
          + {t("routes.newRoute")}
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
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("routes.totalKm")}</th>
              <th className="px-4 py-3">{t("routes.estMinutes")}</th>
              <th className="px-4 py-3">{t("routes.stops")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && routes.length === 0 && !error && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">{t("common.none")}</td></tr>
            )}
            {routes.map((r) => (
              <tr key={r.id} onClick={() => setViewRoute(r)} className="cursor-pointer hover:bg-ink-900/40">
                <td className="px-4 py-3 font-medium text-white">{r.name}</td>
                <td className="px-4 py-3 text-slate-300">{r.total_km ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{r.est_minutes ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{r.stops?.length ?? 0}</td>
                <td className="px-4 py-3"><StatusBadge status={r.is_active ? "active" : "inactive"} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* View stops */}
      <Modal open={viewRoute !== null} onClose={() => setViewRoute(null)} title={viewRoute ? `${viewRoute.name} — ${t("routes.stops")}` : ""}>
        {viewRoute && (
          <ol className="space-y-2">
            {[...viewRoute.stops].sort((a, b) => a.stop_order - b.stop_order).map((s) => (
              <li key={`${s.stop_order}-${s.name}`} className="flex items-center justify-between rounded-lg border border-ink-800 px-3 py-2 text-sm">
                <span className="text-white">{s.stop_order}. {s.name}</span>
                <span className="text-slate-400">{s.lat}, {s.lng} · {t("routes.dwell")} {s.dwell_minutes}m</span>
              </li>
            ))}
            {viewRoute.stops.length === 0 && <li className="text-slate-500">{t("routes.noStops")}</li>}
          </ol>
        )}
      </Modal>

      {/* New route */}
      <Modal open={open} onClose={() => setOpen(false)} title={t("routes.newRoute")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Input label={`${t("common.name")} *`} value={name} onChange={(e) => setName(e.target.value)} required />
          <div className="grid grid-cols-2 gap-3">
            <Input label={t("routes.totalKm")} type="number" step="0.01" min={0} value={totalKm} onChange={(e) => setTotalKm(e.target.value)} />
            <Input label={t("routes.estMinutes")} type="number" min={0} value={estMin} onChange={(e) => setEstMin(e.target.value)} />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-300">{t("routes.stops")}</span>
              <span className="text-xs text-slate-500">{t("routes.mapPickerNote")}</span>
            </div>
            <div className="space-y-2">
              {stops.map((s, i) => (
                <div key={i} className="grid grid-cols-12 items-end gap-2 rounded-lg border border-ink-800 p-2">
                  <div className="col-span-4">
                    <label className="text-xs text-slate-500">{t("common.name")}</label>
                    <input value={s.name} onChange={(e) => updateStop(i, "name", e.target.value)} className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
                  </div>
                  <div className="col-span-3">
                    <label className="text-xs text-slate-500">{t("routes.lat")}</label>
                    <input type="number" step="any" value={s.lat} onChange={(e) => updateStop(i, "lat", e.target.value)} className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
                  </div>
                  <div className="col-span-3">
                    <label className="text-xs text-slate-500">{t("routes.lng")}</label>
                    <input type="number" step="any" value={s.lng} onChange={(e) => updateStop(i, "lng", e.target.value)} className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
                  </div>
                  <div className="col-span-1">
                    <label className="text-xs text-slate-500">{t("routes.dwell")}</label>
                    <input type="number" min={0} value={s.dwell_minutes} onChange={(e) => updateStop(i, "dwell_minutes", e.target.value)} className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
                  </div>
                  <button type="button" onClick={() => removeStop(i)} className="col-span-1 rounded-md border border-red-500/40 py-1.5 text-xs text-red-300 hover:bg-red-500/10">✕</button>
                </div>
              ))}
            </div>
            <button type="button" onClick={addStop} className="mt-2 rounded-md border border-ink-700 px-3 py-1 text-xs text-slate-300 hover:border-brand hover:text-white">+ {t("routes.addStop")}</button>
          </div>

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

"use client";

import { useCallback, useEffect, useState } from "react";
import { getLiveDrivers, type LiveDriver } from "@/lib/manager";
import { useT } from "@/lib/i18n";

type TFn = (k: string) => string;

function agoLabel(iso: string | null, t: TFn): string {
  if (!iso) return t("full.noPings");
  const then = new Date(iso.replace(" ", "T")).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  const pre = t("full.updated");
  if (secs < 60) return `${pre} ${secs}${t("full.secShort")}`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${pre} ${mins}${t("full.minShort")}`;
  return `${pre} ${Math.round(mins / 60)}${t("full.hrShort")}`;
}

export default function FullViewPage() {
  const { t } = useT();
  const [drivers, setDrivers] = useState<LiveDriver[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await getLiveDrivers();
      setDrivers(list);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Poll every 15s so the list feels live.
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const selected = drivers.find((d) => d.driver_id === selectedId) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.fullView")}</h1>
          <p className="text-sm text-slate-400">
            {loading ? t("common.loading") : `${drivers.length} ${t("common.active")} · ${t("full.autoRefresh")}`}
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Map placeholder (drop-in slot for Mapbox) */}
        <div className="lg:col-span-2">
          <div className="flex h-[460px] flex-col items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/40 text-center">
            <div className="mb-2 text-3xl">🗺️</div>
            <div className="font-medium text-slate-300">{t("full.mapPlaceholder")}</div>
            <div className="mt-1 text-sm text-slate-500">
              {t("full.mapSub")}
            </div>
            {selected && (
              <div className="mt-4 rounded-lg border border-brand/30 bg-brand/10 px-4 py-2 text-sm text-brand-sage">
                <strong>{selected.name}</strong>
                {selected.position
                  ? ` — ${selected.position.lat.toFixed(5)}, ${selected.position.lng.toFixed(5)}`
                  : ` — ${t("full.noPosition")}`}
              </div>
            )}
          </div>

          {/* History placeholder */}
          <div className="mt-4 flex h-24 items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/40 text-sm text-slate-500">
            {t("full.history")}
          </div>
        </div>

        {/* Active drivers list */}
        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
          <h2 className="mb-2 px-1 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {t("full.activeDrivers")}
          </h2>
          {!loading && drivers.length === 0 && (
            <div className="px-1 py-6 text-sm text-slate-500">{t("full.noActive")}</div>
          )}
          <ul className="space-y-1.5">
            {drivers.map((d) => {
              const active = d.driver_id === selectedId;
              return (
                <li key={d.driver_id}>
                  <button
                    onClick={() => setSelectedId(d.driver_id)}
                    className={
                      "w-full rounded-lg border px-3 py-2.5 text-left transition-colors " +
                      (active
                        ? "border-brand bg-brand/10"
                        : "border-ink-800 hover:border-ink-700 hover:bg-ink-900/60")
                    }
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-white">{d.name}</span>
                      <span className={d.online ? "inline-flex items-center gap-1.5 text-xs text-brand-sage" : "text-xs text-slate-500"}>
                        {d.online ? <><span className="h-2 w-2 rounded-full bg-brand-sage" />{t("common.online")}</> : t("common.offline")}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-slate-400">
                      bus {d.vehicle_bus_number ?? "—"} · {d.route_name ?? "—"}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {d.position
                        ? `${d.position.lat.toFixed(5)}, ${d.position.lng.toFixed(5)} · ${agoLabel(d.position.recorded_at, t)}`
                        : t("full.noPosition")}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </div>
  );
}

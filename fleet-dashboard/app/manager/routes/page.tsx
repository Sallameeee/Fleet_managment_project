"use client";

import { useCallback, useEffect, useState } from "react";
import { listRoutes, type ManagerRoute } from "@/lib/manager";
import { routeColor } from "@/lib/mapbox";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import StatusBadge from "@/components/StatusBadge";
import RouteEditor from "@/components/RouteEditor";
import RouteDetail from "@/components/RouteDetail";
import RoutesOverview from "@/components/RoutesOverview";

export default function ManagerRoutesPage() {
  const { t } = useT();
  const toast = useToast();
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewRoute, setViewRoute] = useState<ManagerRoute | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editRoute, setEditRoute] = useState<ManagerRoute | null>(null);
  const [overviewOpen, setOverviewOpen] = useState(false);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.routes")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${routes.length}`}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setOverviewOpen(true)}
            disabled={routes.length === 0}
            className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white disabled:opacity-40"
          >
            {t("routes.viewAll")}
          </button>
          <button
            onClick={() => setEditorOpen(true)}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
          >
            + {t("routes.newRoute")}
          </button>
        </div>
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
            {routes.map((r, i) => (
              <tr key={r.id} onClick={() => setViewRoute(r)} className="cursor-pointer hover:bg-ink-900/40">
                <td className="px-4 py-3 font-medium text-white">
                  <span className="inline-flex items-center gap-2">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: routeColor(r.color, i) }} />
                    {r.name}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-300">{r.total_km ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300">{r.est_minutes ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{r.stops?.length ?? 0}</td>
                <td className="px-4 py-3"><StatusBadge status={r.is_active ? "active" : "inactive"} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Route detail (read-only map + timed stops, with Edit / Delete) */}
      {viewRoute && (
        <RouteDetail
          route={viewRoute}
          onClose={() => setViewRoute(null)}
          onEdit={() => {
            setEditRoute(viewRoute);
            setViewRoute(null);
          }}
          onDeleted={() => {
            setViewRoute(null);
            toast.success(t("toast.deleted"));
            load();
          }}
        />
      )}

      {/* All-routes overview (read-only, each route in its color) */}
      {overviewOpen && <RoutesOverview routes={routes} onClose={() => setOverviewOpen(false)} />}

      {/* Map-based route editor (create or edit) */}
      {(editorOpen || editRoute) && (
        <RouteEditor
          route={editRoute ?? undefined}
          onClose={() => {
            setEditorOpen(false);
            setEditRoute(null);
          }}
          onSaved={() => {
            const editing = !!editRoute;
            setEditorOpen(false);
            setEditRoute(null);
            toast.success(editing ? t("toast.saved") : t("toast.created"));
            load();
          }}
        />
      )}
    </div>
  );
}

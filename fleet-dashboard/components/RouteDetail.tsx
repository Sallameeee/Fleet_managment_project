"use client";

import { useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import { deleteRoute, type ManagerRoute } from "@/lib/manager";
import { getDirections, routeColor, addArrowLayer } from "@/lib/mapbox";
import { parseHM, fmtDur } from "@/lib/routeTime";
import { useT } from "@/lib/i18n";
import MapView, { type MapboxMap } from "@/components/MapView";

export default function RouteDetail({
  route,
  onClose,
  onEdit,
  onDeleted,
}: {
  route: ManagerRoute;
  onClose: () => void;
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const { t } = useT();
  const stops = [...route.stops].sort((a, b) => a.stop_order - b.stop_order);
  const color = routeColor(route.color);

  const mapRef = useRef<MapboxMap | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const [deleting, setDeleting] = useState(false);
  const [confirming, setConfirming] = useState(false);

  function drawLine(map: MapboxMap, geometry: GeoJSON.Geometry) {
    const data = { type: "Feature" as const, geometry, properties: {} };
    const src = map.getSource("route") as mapboxgl.GeoJSONSource | undefined;
    if (src) src.setData(data as GeoJSON.Feature);
    else {
      map.addSource("route", { type: "geojson", data: data as GeoJSON.Feature });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": color, "line-width": 5, "line-opacity": 0.85 },
      });
      addArrowLayer(map, "route-arrows", "route"); // direction of travel
    }
  }

  function render(map: MapboxMap) {
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];
    if (stops.length === 0) return;
    const bounds = new mapboxgl.LngLatBounds();
    stops.forEach((s, i) => {
      bounds.extend([s.lng, s.lat]);
      const el = document.createElement("div");
      el.textContent = String(i + 1);
      el.style.cssText =
        `width:26px;height:26px;border-radius:9999px;background:${color};color:#fff;` +
        `display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;` +
        `border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)`;
      markersRef.current.push(new mapboxgl.Marker({ element: el }).setLngLat([s.lng, s.lat]).addTo(map));
    });
    if (stops.length === 1) {
      map.flyTo({ center: [stops[0].lng, stops[0].lat], zoom: 13 });
    } else {
      map.fitBounds(bounds, { padding: 70, maxZoom: 14, duration: 300 });
    }
    // Saved geometry if present, else recompute via Directions.
    if (route.geometry) {
      drawLine(map, route.geometry as unknown as GeoJSON.Geometry);
    } else if (stops.length >= 2) {
      getDirections(stops.map((s) => [s.lng, s.lat])).then((d) => d && drawLine(map, d.geometry));
    }
  }

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    render(map);
  }
  function handleStyleChange(map: MapboxMap) {
    render(map); // setStyle wiped layers; redraw
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => markersRef.current.forEach((m) => m.remove()), []);

  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function doDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteRoute(route.id);
      onDeleted();
    } catch (e) {
      // Keep the dialog open and show WHY (e.g. the 409 "used by N
      // assignment(s)" message) instead of failing silently.
      setDeleteError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setDeleting(false);
    }
  }

  function diffBadge(a: string | null | undefined, b: string | null | undefined) {
    const ma = parseHM(a ?? "");
    const mb = parseHM(b ?? "");
    if (ma === null || mb === null) return { text: "—", invalid: false };
    const d = mb - ma;
    if (d < 0) return { text: t("routes.invalidArrival"), invalid: true };
    return { text: `+${fmtDur(d)}`, invalid: false };
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-950">
      <header className="flex items-center justify-between border-b border-ink-800 px-5 py-3">
        <h2 className="text-lg font-semibold text-white">{route.name}</h2>
        <div className="flex items-center gap-2">
          <button
            onClick={onEdit}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-sage"
          >
            {t("common.edit")}
          </button>
          <button
            onClick={() => { setDeleteError(null); setConfirming(true); }}
            className="rounded-lg border border-red-500/50 px-4 py-2 text-sm font-medium text-red-300 hover:bg-red-500/10"
          >
            {t("common.delete")}
          </button>
          <button onClick={onClose} className="ms-2 text-slate-400 hover:text-white" aria-label="Close">✕</button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Map (read-only, kept LTR so it never mirrors under RTL) */}
        <div dir="ltr" className="relative min-w-0 flex-1">
          <MapView className="h-full w-full" interactive onReady={handleReady} onStyleChange={handleStyleChange} />
        </div>

        {/* Stops + times */}
        <aside className="flex w-96 shrink-0 flex-col gap-3 overflow-y-auto border-s border-ink-800 bg-ink-900/50 p-4">
          <div className="flex gap-2 text-xs">
            <div className="flex-1 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2">
              <div className="text-slate-500">{t("routes.totalKm")}</div>
              <div className="text-sm font-semibold text-white">{route.total_km ?? "—"}</div>
            </div>
            <div className="flex-1 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2">
              <div className="text-slate-500">{t("routes.totalTime")}</div>
              <div className="text-sm font-semibold text-white">
                {route.est_minutes != null ? fmtDur(route.est_minutes) : "—"}
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2 text-xs">
            <span className="text-slate-500">{t("routes.startTime")}: </span>
            <span className="font-medium text-white">{(route.start_time ?? "").slice(0, 5) || "—"}</span>
          </div>

          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            {t("routes.stops")} ({stops.length})
          </h3>

          {stops.length === 0 && <p className="text-xs text-slate-500">{t("routes.noStops")}</p>}

          <ol>
            {stops.map((s, i) => {
              const next = stops[i + 1];
              const badge = next ? diffBadge(s.arrival_time, next.arrival_time) : null;
              return (
                <li key={s.id ?? i}>
                  <div className="rounded-lg border border-ink-800 p-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand text-[10px] font-bold text-white">
                        {i + 1}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm text-white">{s.name}</span>
                    </div>
                    <div className="mt-1 flex items-center gap-3 ps-7 text-[11px] text-slate-400">
                      <span>
                        {t("routes.arrival")}: {(s.arrival_time ?? "").slice(0, 5) || "—"}
                      </span>
                      <span>
                        {t("routes.dwell")}: {s.dwell_minutes}m
                      </span>
                    </div>
                  </div>
                  {badge && (
                    <div className="flex items-center gap-2 py-1 ps-2.5 text-[10px]">
                      <span className="text-slate-600">⋮</span>
                      <span
                        className={
                          "rounded-full border px-2 py-0.5 " +
                          (badge.invalid
                            ? "border-red-500/40 bg-red-500/10 text-red-300"
                            : "border-ink-700 text-slate-400")
                        }
                      >
                        ↓ {badge.text}
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </aside>
      </div>

      {/* Delete confirm */}
      {confirming && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-xl border border-ink-700 bg-ink-900 p-5">
            <h3 className="text-base font-semibold text-white">{t("routes.deleteRoute")}</h3>
            <p className="mt-2 text-sm text-slate-300">{t("routes.deleteConfirm")}</p>
            {deleteError && (
              <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {deleteError}
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => { setDeleteError(null); setConfirming(false); }}
                className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={doDelete}
                disabled={deleting}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-60"
              >
                {deleting ? t("common.loading") : t("common.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

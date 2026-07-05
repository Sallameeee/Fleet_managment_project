"use client";

import { useRef } from "react";
import mapboxgl from "mapbox-gl";
import { type ManagerRoute } from "@/lib/manager";
import { routeColor, addArrowLayer } from "@/lib/mapbox";
import { useT } from "@/lib/i18n";
import MapView, { type MapboxMap } from "@/components/MapView";

// A route's drawable path: its saved geometry, else a straight line through its
// ordered stops (so routes without a computed geometry still appear).
function routeCoords(route: ManagerRoute): [number, number][] {
  const geo = route.geometry;
  if (geo && Array.isArray(geo.coordinates) && geo.coordinates.length > 1) {
    return geo.coordinates as [number, number][];
  }
  return [...route.stops]
    .sort((a, b) => a.stop_order - b.stop_order)
    .map((s) => [s.lng, s.lat] as [number, number]);
}

export default function RoutesOverview({
  routes,
  onClose,
}: {
  routes: ManagerRoute[];
  onClose: () => void;
}) {
  const { t } = useT();
  const mapRef = useRef<MapboxMap | null>(null);
  const boundsRef = useRef<Map<string, mapboxgl.LngLatBounds>>(new Map());

  function render(map: MapboxMap) {
    const all = new mapboxgl.LngLatBounds();
    boundsRef.current = new Map();
    routes.forEach((r, i) => {
      const coords = routeCoords(r);
      const srcId = `ov-${r.id}`;
      const layerId = `ov-line-${r.id}`;
      const arrowId = `ov-arrow-${r.id}`;
      if (map.getLayer(arrowId)) map.removeLayer(arrowId);
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(srcId)) map.removeSource(srcId);
      if (coords.length < 2) return;
      const b = new mapboxgl.LngLatBounds();
      coords.forEach((c) => { b.extend(c); all.extend(c); });
      boundsRef.current.set(r.id, b);
      map.addSource(srcId, {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} },
      });
      map.addLayer({
        id: layerId,
        type: "line",
        source: srcId,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": routeColor(r.color, i), "line-width": 4, "line-opacity": 0.85 },
      });
      addArrowLayer(map, arrowId, srcId); // direction of travel
    });
    if (!all.isEmpty()) map.fitBounds(all, { padding: 60, maxZoom: 14, duration: 300 });
  }

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    render(map);
  }
  function focusRoute(id: string) {
    const b = boundsRef.current.get(id);
    if (b && mapRef.current) mapRef.current.fitBounds(b, { padding: 80, maxZoom: 15, duration: 500 });
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-950">
      <header className="flex items-center justify-between border-b border-ink-800 px-5 py-3">
        <h2 className="text-lg font-semibold text-white">{t("routes.allRoutes")} ({routes.length})</h2>
        <button onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close">✕</button>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Map (LTR so it never mirrors under RTL) */}
        <div dir="ltr" className="relative min-w-0 flex-1">
          <MapView className="h-full w-full" onReady={handleReady} onStyleChange={handleReady} />
        </div>

        {/* Legend */}
        <aside className="w-72 shrink-0 overflow-y-auto border-s border-ink-800 bg-ink-900/50 p-3">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{t("routes.allRoutes")}</h3>
          {routes.length === 0 && <p className="text-xs text-slate-500">{t("common.none")}</p>}
          <ul className="space-y-1">
            {routes.map((r, i) => {
              const hasPath = routeCoords(r).length >= 2;
              return (
                <li key={r.id}>
                  <button
                    onClick={() => focusRoute(r.id)}
                    disabled={!hasPath}
                    className="flex w-full items-center gap-2 rounded-lg border border-ink-800 px-2.5 py-2 text-start text-sm hover:border-ink-700 disabled:opacity-50"
                  >
                    <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: routeColor(r.color, i) }} />
                    <span className="min-w-0 flex-1 truncate text-slate-200">{r.name}</span>
                    <span className="shrink-0 text-[11px] text-slate-500">
                      {hasPath ? (r.total_km != null ? `${r.total_km} km` : `${r.stops?.length ?? 0}`) : t("routes.noGeometry")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
      </div>
    </div>
  );
}

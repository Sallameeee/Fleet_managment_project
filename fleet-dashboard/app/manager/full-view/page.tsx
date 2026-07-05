"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import {
  getDriverPositions,
  listRoutes,
  listDriverGroups,
  listDrivers,
  listCenters,
  type DriverPosition,
  type ManagerRoute,
  type DriverGroup,
  type ManagerDriver,
  type OrgCenter,
} from "@/lib/manager";
import {
  routeColor,
  addArrowLayer,
  nearestRoute,
  pointToSegmentMeters,
  getDirections,
  type LngLat,
} from "@/lib/mapbox";
import { googleAutocomplete, googlePlaceDetails, hasGoogleKey, type Suggestion } from "@/lib/google";
import { useT } from "@/lib/i18n";
import MapView, { type MapboxMap } from "@/components/MapView";
import DriverGroupsPanel from "@/components/DriverGroupsPanel";

type TFn = (k: string) => string;
type Dist = "loading" | { km: number; minutes: number; name?: string } | null;

function fmtDist(d: Dist, t: TFn): string {
  if (d === "loading") return t("full.calc");
  if (!d) return "—";
  return `${d.km} km · ${d.minutes}${t("full.minShort")}`;
}
function fmtMeters(m: number): string {
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(2)} km`;
}
// Best-effort NEXT stop: the end of the route segment the bus is currently
// closest to (nearest-segment + progress heuristic). Documented as an estimate.
function nextStopIndex(stops: { lng: number; lat: number }[], p: LngLat): number {
  if (stops.length < 2) return stops.length - 1;
  let best = Infinity;
  let seg = 0;
  for (let i = 0; i < stops.length - 1; i++) {
    const d = pointToSegmentMeters(p, [stops[i].lng, stops[i].lat], [stops[i + 1].lng, stops[i + 1].lat]);
    if (d < best) {
      best = d;
      seg = i;
    }
  }
  return Math.min(seg + 1, stops.length - 1);
}

function sinceLabel(iso: string | null, t: TFn): string {
  if (!iso) return "—";
  const then = new Date(iso.replace(" ", "T")).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 45) return t("full.justNow");
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}${t("full.minShort")} ${t("full.ago")}`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}${t("full.hrShort")} ${t("full.ago")}`;
  return `${Math.round(hrs / 24)}d ${t("full.ago")}`;
}

function initial(name: string | null): string {
  return (name ?? "?").trim().charAt(0).toUpperCase() || "?";
}

// The route's line geometry for drawing. Uses the EXACT saved geometry verbatim
// (what the manager drew in the editor); only falls back to a straight line
// through the stops when there is no saved geometry at all — never recomputes
// or re-requests Directions.
function routeGeometry(r: ManagerRoute): GeoJSON.LineString | null {
  const geo = r.geometry;
  if (geo && Array.isArray(geo.coordinates) && geo.coordinates.length > 1) {
    return geo as GeoJSON.LineString; // saved geometry, used as-is
  }
  const coords = [...r.stops].sort((a, b) => a.stop_order - b.stop_order).map((s) => [s.lng, s.lat] as [number, number]);
  return coords.length >= 2 ? { type: "LineString", coordinates: coords } : null;
}

export default function FullViewPage() {
  const { t } = useT();
  const [positions, setPositions] = useState<DriverPosition[]>([]);
  const [routesById, setRoutesById] = useState<Record<string, ManagerRoute>>({});
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [shownRoutes, setShownRoutes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [viewMode, setViewMode] = useState<"all" | "groups">("all");
  const [groups, setGroups] = useState<DriverGroup[]>([]);
  const [roster, setRoster] = useState<ManagerDriver[]>([]);
  const [centers, setCenters] = useState<OrgCenter[]>([]);
  const [dist, setDist] = useState<{ next: Dist; end: Dist; center: Dist } | null>(null);

  // Nearest-route tool
  const [nearestMode, setNearestMode] = useState(false);
  const [nearest, setNearest] = useState<{ id: string; meters: number; point: LngLat } | null>(null);
  const [nQuery, setNQuery] = useState("");
  const [nSuggestions, setNSuggestions] = useState<Suggestion[]>([]);

  const mapRef = useRef<MapboxMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const markersRef = useRef<Map<string, mapboxgl.Marker>>(new Map());
  const drawnRef = useRef<string[]>([]);
  const initialFitRef = useRef(false);
  const positionsRef = useRef<DriverPosition[]>(positions);
  positionsRef.current = positions;
  // Cache Directions results so the 5s poll never re-hits the API for the same
  // (from→to) at ~11m resolution.
  const dirCacheRef = useRef<Map<string, { km: number; minutes: number }>>(new Map());
  const nearestModeRef = useRef(nearestMode);
  nearestModeRef.current = nearestMode;
  const routesByIdRef = useRef(routesById);
  routesByIdRef.current = routesById;
  const nearestMarkerRef = useRef<mapboxgl.Marker | null>(null);

  const primaryCenter = centers.find((c) => c.is_primary) ?? centers[0] ?? null;

  // ---- polling (15s) -------------------------------------------------------
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const p = await getDriverPositions();
        if (active) {
          setPositions(p);
          setError(null);
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : t("common.loadFailed"));
      } finally {
        if (active) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 5000); // refresh positions every 5s
    return () => {
      active = false;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listRoutes()
      .then((rs) => setRoutesById(Object.fromEntries(rs.map((r) => [r.id, r]))))
      .catch(() => {});
    listDrivers().then(setRoster).catch(() => {});
    listCenters().then(setCenters).catch(() => {});
  }, []);

  const cachedDirections = useCallback(async (from: LngLat, to: LngLat) => {
    const key = `${from[1].toFixed(4)},${from[0].toFixed(4)}|${to[1].toFixed(4)},${to[0].toFixed(4)}`;
    const hit = dirCacheRef.current.get(key);
    if (hit) return hit;
    const d = await getDirections([from, to]);
    if (d) {
      const v = { km: d.km, minutes: d.minutes };
      dirCacheRef.current.set(key, v);
      return v;
    }
    return null;
  }, []);

  const reloadGroups = useCallback(() => {
    listDriverGroups().then(setGroups).catch(() => {});
  }, []);
  useEffect(() => {
    reloadGroups();
  }, [reloadGroups]);

  const posById: Record<string, DriverPosition> = {};
  positions.forEach((d) => (posById[d.driver_id] = d));

  function selectMany(ids: string[], on: boolean) {
    setSelectedIds((cur) => {
      const set = new Set(cur);
      ids.forEach((id) => (on ? set.add(id) : set.delete(id)));
      return [...set];
    });
  }

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    map.on("click", (e) => {
      if (nearestModeRef.current) computeNearest([e.lngLat.lng, e.lngLat.lat]);
    });
    setMapReady(true);
  }

  // ---- nearest route -------------------------------------------------------
  function computeNearest(point: LngLat) {
    const arr = Object.values(routesByIdRef.current)
      .map((r) => ({ id: r.id, coords: (routeGeometry(r)?.coordinates ?? []) as LngLat[] }))
      .filter((x) => x.coords.length);
    const best = nearestRoute(point, arr);
    if (!best) return;
    setNearest({ ...best, point });
    setShownRoutes((s) => (s.includes(best.id) ? s : [...s, best.id])); // make it visible
  }

  // search a place for the nearest tool
  useEffect(() => {
    if (!nQuery.trim()) {
      setNSuggestions([]);
      return;
    }
    const h = window.setTimeout(() => googleAutocomplete(nQuery).then((r) => setNSuggestions(r.suggestions)), 250);
    return () => window.clearTimeout(h);
  }, [nQuery]);

  async function pickNearestPlace(s: Suggestion) {
    setNSuggestions([]);
    setNQuery("");
    const det = await googlePlaceDetails(s.placeId);
    if (det) {
      mapRef.current?.flyTo({ center: [det.lng, det.lat], zoom: 13 });
      computeNearest([det.lng, det.lat]);
    }
  }

  // nearest highlight marker + fit
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    nearestMarkerRef.current?.remove();
    nearestMarkerRef.current = null;
    if (!nearest) return;
    const el = document.createElement("div");
    el.style.cssText =
      "width:16px;height:16px;border-radius:9999px;background:#f59e0b;border:3px solid #fff;box-shadow:0 0 0 3px rgba(245,158,11,.45),0 1px 4px rgba(0,0,0,.5)";
    nearestMarkerRef.current = new mapboxgl.Marker({ element: el }).setLngLat(nearest.point).addTo(map);
    const r = routesByIdRef.current[nearest.id];
    const g = r ? routeGeometry(r) : null;
    const b = new mapboxgl.LngLatBounds();
    b.extend(nearest.point);
    if (g) (g.coordinates as LngLat[]).forEach((c) => b.extend(c));
    if (!b.isEmpty()) map.fitBounds(b, { padding: 80, maxZoom: 15, duration: 400 });
  }, [nearest, mapReady]);

  // ---- distances for the focused bus (cached) ------------------------------
  const focusedPos = positions.find((d) => d.driver_id === focusedId)?.position ?? null;
  const focusedPosKey = focusedPos ? `${focusedPos.lat.toFixed(4)},${focusedPos.lng.toFixed(4)}` : "";
  useEffect(() => {
    const f = positionsRef.current.find((d) => d.driver_id === focusedId);
    if (!f || !f.position) {
      setDist(null);
      return;
    }
    const bus: LngLat = [f.position.lng, f.position.lat];
    const route = f.route_id ? routesByIdRef.current[f.route_id] : null;
    const stops = route ? [...route.stops].sort((a, b) => a.stop_order - b.stop_order) : [];
    let cancelled = false;
    (async () => {
      setDist({ next: "loading", end: "loading", center: primaryCenter ? "loading" : null });
      let next: Dist = null;
      let end: Dist = null;
      if (stops.length >= 1) {
        const ni = nextStopIndex(stops, bus);
        const ns = stops[ni];
        const ls = stops[stops.length - 1];
        const nd = await cachedDirections(bus, [ns.lng, ns.lat]);
        const ed = await cachedDirections(bus, [ls.lng, ls.lat]);
        next = nd ? { ...nd, name: ns.name } : null;
        end = ed ? { ...ed, name: ls.name } : null;
      }
      let center: Dist = null;
      if (primaryCenter) {
        const cd = await cachedDirections(bus, [primaryCenter.lng, primaryCenter.lat]);
        center = cd ?? null;
      }
      if (!cancelled) setDist({ next, end, center });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedId, focusedPosKey, primaryCenter?.id, cachedDirections]);

  // ---- route lines ---------------------------------------------------------
  const drawRoutes = useCallback(
    (map: MapboxMap) => {
      drawnRef.current.forEach((rid) => {
        const lid = `lr-line-${rid}`;
        const aid = `lr-arrow-${rid}`;
        const sid = `lr-${rid}`;
        if (map.getLayer(aid)) map.removeLayer(aid);
        if (map.getLayer(lid)) map.removeLayer(lid);
        if (map.getSource(sid)) map.removeSource(sid);
      });
      drawnRef.current = [];
      shownRoutes.forEach((rid, i) => {
        const r = routesById[rid];
        if (!r) return;
        const geometry = routeGeometry(r);
        if (!geometry) return;
        const sid = `lr-${rid}`;
        const lid = `lr-line-${rid}`;
        map.addSource(sid, {
          type: "geojson",
          data: { type: "Feature", geometry, properties: {} } as GeoJSON.Feature,
        });
        map.addLayer({
          id: lid,
          type: "line",
          source: sid,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": routeColor(r.color, i), "line-width": 4, "line-opacity": 0.8 },
        });
        addArrowLayer(map, `lr-arrow-${rid}`, sid); // direction of travel
        drawnRef.current.push(rid);
      });
    },
    [shownRoutes, routesById],
  );

  useEffect(() => {
    const map = mapRef.current;
    if (map && mapReady) drawRoutes(map);
  }, [drawRoutes, mapReady]);

  function handleStyleChange(map: MapboxMap) {
    drawnRef.current = []; // setStyle wiped layers
    drawRoutes(map);
  }

  // ---- markers -------------------------------------------------------------
  const selectedKey = [...selectedIds].sort().join(",");
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = new Map();
    positions.forEach((d) => {
      if (!d.position) return;
      const focused = d.driver_id === focusedId;
      const selected = selectedIds.includes(d.driver_id);
      const base = d.online ? "#22c55e" : "#64748b";
      const ring = focused ? "#fde68a" : selected ? "#3AA76D" : "#ffffff";
      const glow = focused
        ? "0 0 0 3px rgba(253,230,138,.5)"
        : selected
          ? "0 0 0 3px rgba(58,167,109,.55)"
          : "0 0 0 0";
      const el = document.createElement("div");
      el.style.cssText = "display:flex;flex-direction:column;align-items:center;cursor:pointer";
      el.title = `${d.name ?? ""}${d.vehicle_bus_number ? " · " + d.vehicle_bus_number : ""}`;
      const dot = document.createElement("div");
      dot.textContent = initial(d.name);
      dot.style.cssText =
        `width:24px;height:24px;border-radius:9999px;background:${base};color:#fff;` +
        `display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;` +
        `border:2px solid ${ring};box-shadow:${glow},0 1px 4px rgba(0,0,0,.4)`;
      el.appendChild(dot);
      if (focused || selected) {
        const label = document.createElement("div");
        label.textContent = d.name ?? "";
        label.style.cssText =
          "margin-top:2px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" +
          "border-radius:6px;background:rgba(15,23,42,.85);padding:1px 5px;font-size:10px;color:#fff";
        el.appendChild(label);
      }
      const marker = new mapboxgl.Marker({ element: el }).setLngLat([d.position.lng, d.position.lat]).addTo(map);
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        setFocusedId(d.driver_id);
      });
      markersRef.current.set(d.driver_id, marker);
    });
    // initial fit once, when nothing is selected/focused
    if (!initialFitRef.current && !selectedIds.length && !focusedId) {
      const withPos = positions.filter((d) => d.position);
      if (withPos.length) {
        const b = new mapboxgl.LngLatBounds();
        withPos.forEach((d) => b.extend([d.position!.lng, d.position!.lat]));
        map.fitBounds(b, { padding: 70, maxZoom: 14, duration: 300 });
        initialFitRef.current = true;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, focusedId, selectedKey, mapReady]);

  // ---- fit to selection / focus (not on every poll) ------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const withPos = positionsRef.current.filter((d) => d.position);
    if (selectedIds.length > 0) {
      const sel = withPos.filter((d) => selectedIds.includes(d.driver_id));
      if (sel.length) {
        const b = new mapboxgl.LngLatBounds();
        sel.forEach((d) => b.extend([d.position!.lng, d.position!.lat]));
        map.fitBounds(b, { padding: 90, maxZoom: 15, duration: 500 });
      }
    } else if (focusedId) {
      const f = withPos.find((d) => d.driver_id === focusedId);
      if (f) map.flyTo({ center: [f.position!.lng, f.position!.lat], zoom: 14, duration: 500 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey, focusedId, mapReady]);

  // ---- selection helpers ---------------------------------------------------
  function toggleSelect(id: string) {
    setSelectedIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }
  const allIds = positions.map((d) => d.driver_id);
  const allSelected = allIds.length > 0 && selectedIds.length === allIds.length;
  function toggleSelectAll() {
    setSelectedIds(allSelected ? [] : allIds);
  }

  function toggleRoute(routeId: string) {
    setShownRoutes((r) => (r.includes(routeId) ? r.filter((x) => x !== routeId) : [...r, routeId]));
  }

  const focused = positions.find((d) => d.driver_id === focusedId) ?? null;
  const onlineCount = positions.filter((d) => d.online).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.fullView")}</h1>
          <p className="text-sm text-slate-400">
            {loading ? t("common.loading") : `${onlineCount} ${t("common.online")} / ${positions.length} · ${t("full.autoRefresh")}`}
          </p>
        </div>
      </div>

      {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Map (LTR so it never mirrors under RTL) */}
        <div dir="ltr" className="relative lg:col-span-2">
          <div className="h-[600px] overflow-hidden rounded-xl border border-ink-800">
            <MapView className="h-full w-full" styleSwitcher onReady={handleReady} onStyleChange={handleStyleChange} />
          </div>

          {/* Route legend */}
          {shownRoutes.length > 0 && (
            <div className="absolute bottom-3 left-3 z-10 max-w-[240px] rounded-lg border border-ink-700 bg-ink-900/90 p-2 text-xs backdrop-blur">
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="font-semibold text-slate-300">{t("full.routesShown")}</span>
                <button onClick={() => setShownRoutes([])} className="text-brand-sage hover:underline">{t("full.clearRoutes")}</button>
              </div>
              <ul className="space-y-1">
                {shownRoutes.map((rid, i) => (
                  <li key={rid} className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: routeColor(routesById[rid]?.color, i) }} />
                    <span className="min-w-0 flex-1 truncate text-slate-200">{routesById[rid]?.name ?? "—"}</span>
                    <button onClick={() => toggleRoute(rid)} className="shrink-0 text-slate-500 hover:text-white" title={t("full.hideRoute")}>✕</button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Info card */}
          {focused && (
            <div className="absolute left-3 top-3 z-10 w-72 rounded-xl border border-ink-700 bg-ink-900/95 p-3 shadow-2xl backdrop-blur">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-semibold text-white">{focused.name}</div>
                  <div className="mt-0.5 flex items-center gap-1.5 text-xs">
                    <span className={"h-2 w-2 rounded-full " + (focused.online ? "bg-green-500" : "bg-slate-500")} />
                    <span className={focused.online ? "text-green-400" : "text-slate-400"}>
                      {focused.online ? t("common.online") : t("common.offline")}
                    </span>
                    <span className="text-slate-500">· {t("full.lastSeen")} {sinceLabel(focused.position?.recorded_at ?? null, t)}</span>
                  </div>
                </div>
                <button onClick={() => setFocusedId(null)} className="shrink-0 text-slate-400 hover:text-white" aria-label="Close">✕</button>
              </div>

              <div className="mt-2 space-y-1 border-t border-ink-800 pt-2 text-xs text-slate-300">
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">{t("common.vehicle")}</span>
                  <span>{focused.vehicle_bus_number ?? "—"}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">{focused.on_trip ? t("full.onTripNow") : t("full.lastTrip")}</span>
                  <span>{focused.on_trip ? "—" : sinceLabel(focused.last_ended_at, t)}</span>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-slate-500">{t("common.route")}</span>
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="truncate">{focused.route_name ?? t("full.noAssignment")}</span>
                    {focused.route_id && (
                      <button
                        onClick={() => toggleRoute(focused.route_id!)}
                        title={shownRoutes.includes(focused.route_id) ? t("full.hideRoute") : t("full.showRoute")}
                        aria-label={t("full.showRoute")}
                        className={"shrink-0 rounded-md border p-1 " + (shownRoutes.includes(focused.route_id) ? "border-brand bg-brand/15 text-brand-sage" : "border-ink-700 text-slate-300 hover:text-white")}
                      >
                        {shownRoutes.includes(focused.route_id) ? (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
                        ) : (
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z" /><circle cx="12" cy="12" r="3" /></svg>
                        )}
                      </button>
                    )}
                  </span>
                </div>
                {focused.assignment_window && (
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-500">{t("full.currentAssignment")}</span>
                    <span>{focused.assignment_window}</span>
                  </div>
                )}
                {focused.assignment_count > 1 && (
                  <div className="text-[11px] text-slate-500">{focused.assignment_count} {t("full.assignmentsToday")}</div>
                )}
              </div>

              {/* Distances (road, cached) */}
              <div className="mt-2 space-y-1 border-t border-ink-800 pt-2 text-xs text-slate-300">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{t("full.distances")}</div>
                <div className="flex justify-between gap-2">
                  <span className="min-w-0 truncate text-slate-500">
                    {t("full.toNextStop")}{dist?.next && dist.next !== "loading" && dist.next.name ? ` · ${dist.next.name}` : ""}
                  </span>
                  <span className="shrink-0">{fmtDist(dist?.next ?? null, t)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">{t("full.toRouteEnd")}</span>
                  <span className="shrink-0">{fmtDist(dist?.end ?? null, t)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-slate-500">{t("full.toCenter")}</span>
                  <span className="shrink-0">{primaryCenter ? fmtDist(dist?.center ?? null, t) : t("full.noCenter")}</span>
                </div>
              </div>
            </div>
          )}

          {/* Nearest-route tool */}
          <div className="absolute left-1/2 top-3 z-10 -translate-x-1/2">
            <button
              onClick={() => { setNearestMode((v) => !v); if (nearestMode) setNearest(null); }}
              className={"rounded-lg border px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur " + (nearestMode ? "border-amber-500 bg-amber-500/20 text-amber-200" : "border-ink-700 bg-ink-900/90 text-slate-200 hover:text-white")}
            >
              {t("full.nearestRoute")}
            </button>
            {nearestMode && (
              <div className="mt-1 w-64 rounded-lg border border-ink-700 bg-ink-900/95 p-2 shadow-2xl backdrop-blur">
                <p className="mb-1 text-[11px] text-slate-400">{t("full.nearestHint")}</p>
                <input
                  value={nQuery}
                  onChange={(e) => setNQuery(e.target.value)}
                  placeholder={hasGoogleKey() ? t("full.searchPlace") : t("routes.googleMissing")}
                  disabled={!hasGoogleKey()}
                  className="w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-slate-100 focus:border-brand focus:outline-none disabled:opacity-60"
                />
                {nSuggestions.length > 0 && (
                  <ul className="mt-1 max-h-40 overflow-auto rounded-md border border-ink-700 bg-ink-900">
                    {nSuggestions.map((s) => (
                      <li key={s.placeId}><button onClick={() => pickNearestPlace(s)} className="block w-full px-2 py-1.5 text-start text-[11px] text-slate-200 hover:bg-ink-800">{s.description}</button></li>
                    ))}
                  </ul>
                )}
                {nearest && (
                  <div className="mt-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-200">
                    <span className="font-semibold">{routesById[nearest.id]?.name ?? "—"}</span> · {fmtMeters(nearest.meters)} {t("full.nearestAway")}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Driver list */}
        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
          {/* view-mode toggle */}
          <div className="mb-2 inline-flex rounded-lg border border-ink-700 p-0.5">
            {(["all", "groups"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setViewMode(m)}
                className={"rounded-md px-3 py-1 text-xs font-medium transition-colors " + (viewMode === m ? "bg-brand text-white" : "text-slate-400 hover:text-white")}
              >
                {m === "all" ? t("full.viewAll") : t("full.viewGroups")}
              </button>
            ))}
          </div>

          {viewMode === "groups" ? (
            <DriverGroupsPanel
              groups={groups}
              roster={roster}
              posById={posById}
              selectedIds={selectedIds}
              focusedId={focusedId}
              onToggleSelect={toggleSelect}
              onSelectMany={selectMany}
              onFocus={setFocusedId}
              onReload={reloadGroups}
            />
          ) : (
          <>
          <div className="mb-2 flex items-center justify-between px-1">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{t("full.drivers")}</h2>
            {positions.length > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-slate-400">
                <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} className="h-3.5 w-3.5 accent-[#3AA76D]" />
                {t("full.selectAll")}
              </label>
            )}
          </div>
          {selectedIds.length > 0 && (
            <div className="mb-2 px-1 text-xs text-brand-sage">{selectedIds.length} {t("full.selectedCount")}</div>
          )}

          {!loading && positions.length === 0 && <div className="px-1 py-6 text-sm text-slate-500">{t("full.noDrivers")}</div>}

          <ul className="max-h-[560px] space-y-1.5 overflow-y-auto">
            {positions.map((d) => {
              const isFocused = d.driver_id === focusedId;
              const isSelected = selectedIds.includes(d.driver_id);
              return (
                <li key={d.driver_id}>
                  <div
                    className={
                      "flex items-start gap-2 rounded-lg border px-2.5 py-2 transition-colors " +
                      (isFocused ? "border-brand bg-brand/10" : isSelected ? "border-brand/40 bg-brand/5" : "border-ink-800 hover:border-ink-700")
                    }
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelect(d.driver_id)}
                      className="mt-1 h-3.5 w-3.5 shrink-0 accent-[#3AA76D]"
                      aria-label={d.name ?? ""}
                    />
                    <button onClick={() => setFocusedId(d.driver_id)} className="min-w-0 flex-1 text-start">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium text-white">{d.name}</span>
                        <span className={d.online ? "inline-flex shrink-0 items-center gap-1 text-xs text-green-400" : "shrink-0 text-xs text-slate-500"}>
                          {d.online ? <><span className="h-2 w-2 rounded-full bg-green-500" />{t("common.online")}</> : t("common.offline")}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-xs text-slate-400">
                        {d.vehicle_bus_number ?? "—"} · {d.route_name ?? t("full.noAssignment")}
                        {d.assignment_window ? ` · ${d.assignment_window}` : ""}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-500">
                        {d.position ? `${t("full.lastSeen")} ${sinceLabel(d.position.recorded_at, t)}` : t("full.noPosition")}
                      </div>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
          </>
          )}
        </div>
      </div>
    </div>
  );
}

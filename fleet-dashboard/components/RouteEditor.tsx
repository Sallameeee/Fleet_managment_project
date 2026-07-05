"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import { createRoute, updateRoute, type ManagerRoute } from "@/lib/manager";
import {
  reverseGeocode,
  getDirections,
  type DirectionsResult,
  DEFAULT_ROUTE_COLOR,
  ROUTE_PALETTE,
} from "@/lib/mapbox";
import { parseHM, fmtDur } from "@/lib/routeTime";
import {
  googleAutocomplete,
  googlePlaceDetails,
  hasGoogleKey,
  type Suggestion,
  type GoogleErrorCode,
} from "@/lib/google";
import { useT } from "@/lib/i18n";
import MapView, { type MapboxMap } from "@/components/MapView";
import Button from "@/components/Button";

interface EStop {
  id: string;
  name: string;
  lng: number;
  lat: number;
  arrival: string; // "HH:MM"
  dwell: number;
}
// A shaping point: forces the road path through it for one segment (seg = the
// stop index it sits after). Not a stop; used only to reroute via Directions.
interface ShapePoint {
  id: string;
  seg: number;
  lng: number;
  lat: number;
}

function segDist(p: [number, number], a: [number, number], b: [number, number]): number {
  const [px, py] = p, [ax, ay] = a, [bx, by] = b;
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy || 1e-12;
  let t = ((px - ax) * dx + (py - ay) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx, cy = ay + t * dy;
  return Math.hypot(px - cx, py - cy);
}
function nearestSegment(stops: EStop[], lng: number, lat: number): number {
  let idx = 0, best = Infinity;
  for (let i = 0; i < stops.length - 1; i++) {
    const d = segDist([lng, lat], [stops[i].lng, stops[i].lat], [stops[i + 1].lng, stops[i + 1].lat]);
    if (d < best) { best = d; idx = i; }
  }
  return idx;
}
// Directions waypoints = stops in order, with each segment's shaping points
// interleaved right after the stop that starts the segment.
function directionCoords(stops: EStop[], shapes: ShapePoint[]): [number, number][] {
  const out: [number, number][] = [];
  stops.forEach((s, i) => {
    out.push([s.lng, s.lat]);
    shapes.filter((sp) => sp.seg === i).forEach((sp) => out.push([sp.lng, sp.lat]));
  });
  return out;
}

let seedCounter = 0;
const seedId = () => `seed-${++seedCounter}`;

type Menu = { id: string; lng: number; lat: number } | null;
type LineAdd = { lng: number; lat: number; insertAfter: number } | null;
type PendingAdd = { lng: number; lat: number } | null;

export default function RouteEditor({
  onClose,
  onSaved,
  route,
}: {
  onClose: () => void;
  onSaved: () => void;
  route?: ManagerRoute;
}) {
  const { t } = useT();
  const editing = !!route;

  const [name, setName] = useState(route?.name ?? "");
  const [startTime, setStartTime] = useState((route?.start_time ?? "").slice(0, 5));
  const [color, setColor] = useState(route?.color ?? DEFAULT_ROUTE_COLOR);
  const [stops, setStops] = useState<EStop[]>(
    route
      ? [...route.stops]
          .sort((a, b) => a.stop_order - b.stop_order)
          .map((s) => ({
            id: seedId(),
            name: s.name,
            lng: s.lng,
            lat: s.lat,
            arrival: (s.arrival_time ?? "").slice(0, 5),
            dwell: s.dwell_minutes ?? 0,
          }))
      : [],
  );
  const [shapePoints, setShapePoints] = useState<ShapePoint[]>([]);
  const [dir, setDir] = useState<DirectionsResult | null>(null);
  const [routing, setRouting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [moveId, setMoveId] = useState<string | null>(null);

  const [menu, setMenu] = useState<Menu>(null);
  const [lineAdd, setLineAdd] = useState<LineAdd>(null);
  const [pendingAdd, setPendingAdd] = useState<PendingAdd>(null);
  const [overlayTick, setOverlayTick] = useState(0);

  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [searchErr, setSearchErr] = useState<GoogleErrorCode | null>(null);
  const [searching, setSearching] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ stop: EStop; index: number } | null>(null);

  const mapRef = useRef<MapboxMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const markersRef = useRef<Map<string, mapboxgl.Marker>>(new Map());
  const shapeMarkersRef = useRef<Map<string, mapboxgl.Marker>>(new Map());
  const listRefs = useRef<Map<string, HTMLLIElement | null>>(new Map());
  const idRef = useRef(0);
  const stopsRef = useRef<EStop[]>(stops);
  stopsRef.current = stops;
  const menuRef = useRef<Menu>(menu);
  menuRef.current = menu;
  const lineAddRef = useRef<LineAdd>(lineAdd);
  lineAddRef.current = lineAdd;
  const pendingAddRef = useRef<PendingAdd>(pendingAdd);
  pendingAddRef.current = pendingAdd;
  const colorRef = useRef(color);
  colorRef.current = color;
  const interactingRef = useRef(false);
  const dragSuppressRef = useRef(false);
  const newId = () => String(++idRef.current);

  const closeOverlays = useCallback(() => {
    setMenu(null);
    setLineAdd(null);
    setPendingAdd(null);
  }, []);

  // Adding/removing/reordering stops shifts segment indices, which would
  // invalidate shaping points — so we drop them on any structural change.
  function clearShaping() {
    setShapePoints([]);
  }

  // ---- add / mutate stops --------------------------------------------------
  const addStop = useCallback(
    async (lng: number, lat: number, presetName?: string) => {
      const id = newId();
      const fallback = `${t("routes.stop")} ${stopsRef.current.length + 1}`;
      setShapePoints([]);
      setStops((s) => [...s, { id, name: presetName || fallback, lng, lat, arrival: "", dwell: 0 }]);
      setSelectedId(id);
      if (!presetName) {
        const rn = await reverseGeocode(lng, lat);
        if (rn) setStops((s) => s.map((st) => (st.id === id ? { ...st, name: rn } : st)));
      }
    },
    [t],
  );

  function updateStop(id: string, patch: Partial<EStop>) {
    setStops((s) => s.map((st) => (st.id === id ? { ...st, ...patch } : st)));
  }
  function removeStop(id: string) {
    clearShaping();
    setStops((s) => {
      const idx = s.findIndex((x) => x.id === id);
      if (idx >= 0) setUndo({ stop: s[idx], index: idx });
      return s.filter((x) => x.id !== id);
    });
    if (selectedId === id) setSelectedId(null);
  }
  function doUndo() {
    if (!undo) return;
    clearShaping();
    setStops((s) => {
      const c = [...s];
      c.splice(Math.min(undo.index, c.length), 0, undo.stop);
      return c;
    });
    setUndo(null);
  }

  // ---- marker context-menu actions -----------------------------------------
  function startMove(id: string) {
    setMenu(null);
    setSelectedId(id);
    setMoveId(id);
  }
  function duplicateAt(id: string) {
    const cur = stopsRef.current;
    const idx = cur.findIndex((x) => x.id === id);
    if (idx < 0) return;
    const orig = cur[idx];
    let { lng, lat } = orig;
    const map = mapRef.current;
    if (map) {
      const p = map.project([orig.lng, orig.lat]);
      const np = map.unproject([p.x + 30, p.y + 30]);
      lng = np.lng;
      lat = np.lat;
    }
    const cid = newId();
    const copy: EStop = { id: cid, name: orig.name, lng, lat, arrival: orig.arrival, dwell: orig.dwell };
    clearShaping();
    setStops((s) => {
      const i = s.findIndex((x) => x.id === id);
      const c = [...s];
      c.splice(i < 0 ? c.length : i + 1, 0, copy);
      return c;
    });
    setSelectedId(cid);
    setMenu({ id: cid, lng, lat });
  }
  function removeFromMenu(id: string) {
    setMenu(null);
    removeStop(id);
  }

  // ---- line + / reshape ----------------------------------------------------
  function confirmLineInsert() {
    const la = lineAddRef.current;
    if (!la) return;
    const { lng, lat, insertAfter } = la;
    const id = newId();
    clearShaping();
    setStops((s) => {
      const c = [...s];
      c.splice(insertAfter + 1, 0, { id, name: "…", lng, lat, arrival: "", dwell: 0 });
      return c;
    });
    setSelectedId(id);
    setLineAdd(null);
    reverseGeocode(lng, lat).then(
      (n) => n && setStops((s) => s.map((st) => (st.id === id ? { ...st, name: n } : st))),
    );
  }
  function startReshape() {
    const la = lineAddRef.current;
    if (!la) return;
    setShapePoints((sp) => [...sp, { id: newId(), seg: la.insertAfter, lng: la.lng, lat: la.lat }]);
    setLineAdd(null);
  }
  function confirmPendingAdd() {
    const pa = pendingAddRef.current;
    if (!pa) return;
    setPendingAdd(null);
    void addStop(pa.lng, pa.lat);
  }

  // ---- map ----------------------------------------------------------------
  const drawRoute = useCallback((map: MapboxMap, geometry: DirectionsResult["geometry"], lineColor: string) => {
    const data = { type: "Feature" as const, geometry, properties: {} };
    const src = map.getSource("route") as mapboxgl.GeoJSONSource | undefined;
    if (src) {
      src.setData(data as GeoJSON.Feature);
      if (map.getLayer("route-line")) map.setPaintProperty("route-line", "line-color", lineColor);
    } else {
      map.addSource("route", { type: "geojson", data: data as GeoJSON.Feature });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": lineColor, "line-width": 5, "line-opacity": 0.85 },
      });
      map.on("mouseenter", "route-line", () => (map.getCanvas().style.cursor = "copy"));
      map.on("mouseleave", "route-line", () => (map.getCanvas().style.cursor = ""));
    }
  }, []);
  const clearRoute = useCallback((map: MapboxMap) => {
    if (map.getLayer("route-line")) map.removeLayer("route-line");
    if (map.getSource("route")) map.removeSource("route");
  }, []);

  function handleMapReady(map: MapboxMap) {
    mapRef.current = map;

    // General click: dismiss overlays first; else on empty road propose a "+"
    // (does NOT add a stop until the + is clicked). Line clicks handled below.
    map.on("click", (e) => {
      if (interactingRef.current) return;
      if (menuRef.current || lineAddRef.current || pendingAddRef.current) {
        closeOverlays();
        return;
      }
      const onLine =
        map.getLayer("route-line") &&
        map.queryRenderedFeatures(e.point, { layers: ["route-line"] }).length > 0;
      if (onLine) return;
      setPendingAdd({ lng: e.lngLat.lng, lat: e.lngLat.lat });
    });

    // Quick click on the line → offer "+" (insert) and a reshape handle for
    // that segment. Press-drag on the line still pans (we don't preventDefault).
    map.on("click", "route-line", (e) => {
      const base = stopsRef.current;
      if (base.length < 2) return;
      const insertAfter = nearestSegment(base, e.lngLat.lng, e.lngLat.lat);
      setMenu(null);
      setPendingAdd(null);
      setLineAdd({ lng: e.lngLat.lng, lat: e.lngLat.lat, insertAfter });
    });

    setMapReady(true);
  }

  function handleStyleChange(map: MapboxMap) {
    if (dir) drawRoute(map, dir.geometry, colorRef.current);
  }

  // Esc closes any open overlay.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeOverlays();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeOverlays]);

  const stopKey = stops.map((s) => `${s.id}:${s.lng.toFixed(5)},${s.lat.toFixed(5)}`).join(";");
  const shapeKey = shapePoints.map((sp) => `${sp.id}@${sp.seg}:${sp.lng.toFixed(5)},${sp.lat.toFixed(5)}`).join(";");

  // stop markers (rebuild on coords/order/selection/move/color change)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = new Map();
    stops.forEach((s, i) => {
      const selected = s.id === selectedId;
      const moving = s.id === moveId;
      const ring = moving ? "#f59e0b" : selected ? "#fde68a" : "#fff";
      const glow = moving
        ? "0 0 0 4px rgba(245,158,11,.45)"
        : selected
          ? "0 0 0 3px rgba(253,230,138,.5)"
          : "0 0 0 0";
      const el = document.createElement("div");
      el.textContent = String(i + 1);
      el.style.cssText =
        `width:26px;height:26px;border-radius:9999px;background:${color};color:#fff;` +
        `display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;` +
        `cursor:${moving ? "grab" : "pointer"};border:2px solid ${ring};` +
        `box-shadow:${glow},0 1px 4px rgba(0,0,0,.4)`;
      const marker = new mapboxgl.Marker({ element: el, draggable: moving })
        .setLngLat([s.lng, s.lat])
        .addTo(map);
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (dragSuppressRef.current) {
          dragSuppressRef.current = false;
          return;
        }
        setSelectedId(s.id);
        setLineAdd(null);
        setPendingAdd(null);
        setMenu({ id: s.id, lng: s.lng, lat: s.lat });
      });
      marker.on("dragstart", () => {
        interactingRef.current = true;
        dragSuppressRef.current = true;
        setMenu(null);
      });
      marker.on("dragend", () => {
        const ll = marker.getLngLat();
        updateStop(s.id, { lng: ll.lng, lat: ll.lat });
        setMoveId(null);
        window.setTimeout(() => (interactingRef.current = false), 60);
      });
      markersRef.current.set(s.id, marker);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopKey, selectedId, moveId, color, mapReady]);

  // reshape handle markers (draggable diamonds)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    shapeMarkersRef.current.forEach((m) => m.remove());
    shapeMarkersRef.current = new Map();
    shapePoints.forEach((sp) => {
      const el = document.createElement("div");
      el.title = t("routes.reshape");
      el.style.cssText =
        `width:15px;height:15px;transform:rotate(45deg);background:${color};` +
        `border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.5);cursor:grab`;
      const marker = new mapboxgl.Marker({ element: el, draggable: true })
        .setLngLat([sp.lng, sp.lat])
        .addTo(map);
      marker.on("dragstart", () => (interactingRef.current = true));
      marker.on("dragend", () => {
        const ll = marker.getLngLat();
        setShapePoints((list) => list.map((x) => (x.id === sp.id ? { ...x, lng: ll.lng, lat: ll.lat } : x)));
        window.setTimeout(() => (interactingRef.current = false), 60);
      });
      shapeMarkersRef.current.set(sp.id, marker);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shapeKey, color, mapReady]);

  // directions (reroute on stop/shape change)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    let cancelled = false;
    if (stops.length >= 2) {
      setRouting(true);
      getDirections(directionCoords(stops, shapePoints))
        .then((d) => {
          if (cancelled) return;
          setDir(d);
          if (d) {
            drawRoute(map, d.geometry, colorRef.current);
            const b = new mapboxgl.LngLatBounds();
            stops.forEach((s) => b.extend([s.lng, s.lat]));
            map.fitBounds(b, { padding: 70, maxZoom: 14, duration: 400 });
          }
        })
        .finally(() => !cancelled && setRouting(false));
    } else {
      setDir(null);
      clearRoute(map);
      if (stops.length === 1) map.flyTo({ center: [stops[0].lng, stops[0].lat], zoom: 13 });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopKey, shapeKey, mapReady]);

  // live line recolor without a reroute
  useEffect(() => {
    const map = mapRef.current;
    if (map && mapReady && map.getLayer("route-line")) map.setPaintProperty("route-line", "line-color", color);
  }, [color, mapReady]);

  // keep overlays glued to their point as the map moves
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || (!menu && !lineAdd && !pendingAdd)) return;
    const onMove = () => setOverlayTick((n) => n + 1);
    map.on("move", onMove);
    return () => {
      map.off("move", onMove);
    };
  }, [menu, lineAdd, pendingAdd, mapReady]);

  // selecting a stop scrolls it into view in the list
  useEffect(() => {
    if (selectedId) listRefs.current.get(selectedId)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedId]);

  // ---- Google search (debounced autocomplete) ------------------------------
  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([]);
      setSearchErr(null);
      return;
    }
    setSearching(true);
    const h = window.setTimeout(() => {
      googleAutocomplete(query).then(({ suggestions, error }) => {
        setSuggestions(suggestions);
        setSearchErr(error);
        setSearching(false);
      });
    }, 250);
    return () => window.clearTimeout(h);
  }, [query]);

  const searchErrMsg = (code: GoogleErrorCode): string =>
    ({
      "missing-key": t("routes.googleMissing"),
      "auth-failed": t("routes.search.errAuth"),
      "load-failed": t("routes.search.errLoad"),
      "request-denied": t("routes.search.errDenied"),
      unknown: t("routes.search.errUnknown"),
    })[code];

  async function pickSuggestion(s: Suggestion) {
    setSuggestions([]);
    setQuery("");
    const det = await googlePlaceDetails(s.placeId);
    if (!det) return;
    await addStop(det.lng, det.lat, det.name);
    mapRef.current?.flyTo({ center: [det.lng, det.lat], zoom: 14 });
  }

  // ---- reorder -------------------------------------------------------------
  const dragIndexRef = useRef<number | null>(null);
  function onListDrop(toIndex: number) {
    const from = dragIndexRef.current;
    dragIndexRef.current = null;
    if (from === null || from === toIndex) return;
    clearShaping();
    setStops((s) => {
      const c = [...s];
      const [moved] = c.splice(from, 1);
      c.splice(toIndex, 0, moved);
      return c;
    });
  }

  // ---- save ----------------------------------------------------------------
  async function save() {
    if (!name.trim()) {
      setError(t("routes.needName"));
      return;
    }
    if (stops.length < 2) {
      setError(t("routes.needTwoStops"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        start_time: startTime || undefined,
        color,
        total_km: dir?.km,
        est_minutes: dir?.minutes,
        geometry: dir?.geometry,
        stops: stops.map((s, i) => ({
          name: s.name.trim() || `${t("routes.stop")} ${i + 1}`,
          lat: s.lat,
          lng: s.lng,
          stop_order: i + 1,
          dwell_minutes: s.dwell,
          arrival_time: s.arrival || null,
        })),
      };
      if (editing && route) await updateRoute(route.id, payload);
      else await createRoute(payload);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  function diffBadge(a: string, b: string) {
    const ma = parseHM(a);
    const mb = parseHM(b);
    if (ma === null || mb === null) return { text: "—", invalid: false };
    const d = mb - ma;
    if (d < 0) return { text: t("routes.invalidArrival"), invalid: true };
    return { text: `+${fmtDur(d)}`, invalid: false };
  }

  // ---- overlay geometry ----------------------------------------------------
  const map = mapRef.current;
  let menuPos: { x: number; y: number } | null = null;
  let linePos: { x: number; y: number } | null = null;
  let pendingPos: { x: number; y: number } | null = null;
  if (map && mapReady) {
    const cont = map.getContainer();
    const W = cont.clientWidth;
    const H = cont.clientHeight;
    if (menu) {
      const p = map.project([menu.lng, menu.lat]);
      const below = p.y < 84;
      menuPos = { x: Math.max(70, Math.min(W - 70, p.x)), y: below ? Math.min(H - 24, p.y + 22) : Math.max(24, p.y - 46) };
    }
    if (lineAdd) {
      const p = map.project([lineAdd.lng, lineAdd.lat]);
      const below = p.y < 84;
      linePos = { x: Math.max(60, Math.min(W - 60, p.x)), y: below ? Math.min(H - 24, p.y + 22) : Math.max(24, p.y - 46) };
    }
    if (pendingAdd) {
      const p = map.project([pendingAdd.lng, pendingAdd.lat]);
      pendingPos = { x: Math.max(20, Math.min(W - 20, p.x)), y: Math.max(20, Math.min(H - 20, p.y)) };
    }
  }

  const circleBtn =
    "flex h-9 w-9 items-center justify-center rounded-full border border-ink-700 bg-ink-900/95 text-white shadow-lg transition-colors hover:bg-ink-800";
  const circleAdd =
    "flex h-9 w-9 items-center justify-center rounded-full bg-brand text-lg font-bold leading-none text-white shadow-lg ring-2 ring-white transition hover:brightness-110";

  const topHint = moveId ? t("routes.moveHint") : pendingAdd ? t("routes.pendingAddHint") : t("routes.mapHint");

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-950">
      <header className="flex items-center justify-between border-b border-ink-800 px-5 py-3">
        <h2 className="text-lg font-semibold text-white">{editing ? t("routes.editRoute") : t("routes.newRoute")}</h2>
        <button onClick={onClose} className="text-slate-400 hover:text-white" aria-label="Close">✕</button>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Map (kept LTR so it never mirrors under Arabic/RTL) */}
        <div dir="ltr" className="relative min-w-0 flex-1">
          <MapView className="h-full w-full" onReady={handleMapReady} onStyleChange={handleStyleChange} />

          <div className="pointer-events-none absolute left-3 top-3 z-10 max-w-sm rounded-lg bg-ink-900/80 px-3 py-1.5 text-xs text-slate-300 backdrop-blur">
            {topHint}
          </div>

          {/* Marker context menu */}
          {menu && menuPos && (
            <div data-tick={overlayTick} className="absolute z-20 -translate-x-1/2 -translate-y-1/2" style={{ left: menuPos.x, top: menuPos.y }}>
              <div className="flex items-center gap-2 rounded-full bg-ink-900/95 p-1.5 shadow-2xl ring-1 ring-ink-700 backdrop-blur">
                <button className={circleBtn} title={t("routes.move")} aria-label={t("routes.move")} onClick={(e) => { e.stopPropagation(); startMove(menu.id); }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="5 9 2 12 5 15" /><polyline points="9 5 12 2 15 5" /><polyline points="15 19 12 22 9 19" /><polyline points="19 9 22 12 19 15" /><line x1="2" y1="12" x2="22" y2="12" /><line x1="12" y1="2" x2="12" y2="22" /></svg>
                </button>
                <button className={circleBtn} title={t("routes.duplicate")} aria-label={t("routes.duplicate")} onClick={(e) => { e.stopPropagation(); duplicateAt(menu.id); }}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
                </button>
                <button className={circleBtn + " hover:bg-red-500/20"} title={t("routes.remove")} aria-label={t("routes.remove")} onClick={(e) => { e.stopPropagation(); removeFromMenu(menu.id); }}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fca5a5" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                </button>
              </div>
            </div>
          )}

          {/* Line click → add "+" OR reshape handle */}
          {lineAdd && linePos && (
            <div data-tick={overlayTick} className="absolute z-20 -translate-x-1/2 -translate-y-1/2" style={{ left: linePos.x, top: linePos.y }}>
              <div className="flex items-center gap-2 rounded-full bg-ink-900/95 p-1.5 shadow-2xl ring-1 ring-ink-700 backdrop-blur">
                <button className={circleAdd} title={t("routes.addStopHere")} aria-label={t("routes.addStopHere")} onClick={(e) => { e.stopPropagation(); confirmLineInsert(); }}>+</button>
                <button className={circleBtn} title={t("routes.reshape")} aria-label={t("routes.reshape")} onClick={(e) => { e.stopPropagation(); startReshape(); }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="5 9 2 12 5 15" /><polyline points="9 5 12 2 15 5" /><polyline points="15 19 12 22 9 19" /><polyline points="19 9 22 12 19 15" /><line x1="2" y1="12" x2="22" y2="12" /><line x1="12" y1="2" x2="12" y2="22" /></svg>
                </button>
              </div>
            </div>
          )}

          {/* Empty-road click → proposed "+" (adds only when clicked) */}
          {pendingAdd && pendingPos && (
            <button
              data-tick={overlayTick}
              onClick={(e) => { e.stopPropagation(); confirmPendingAdd(); }}
              title={t("routes.addStopHere")}
              aria-label={t("routes.addStopHere")}
              className={circleAdd + " absolute z-20 -translate-x-1/2 -translate-y-1/2"}
              style={{ left: pendingPos.x, top: pendingPos.y }}
            >
              +
            </button>
          )}
        </div>

        {/* Side panel */}
        <aside className="flex w-96 shrink-0 flex-col border-s border-ink-800 bg-ink-900/50">
          <div className="flex-1 space-y-4 overflow-y-auto p-4 pb-4">
            {/* Section 1 — basics */}
            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("routes.basics")}</h3>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("routes.routeNamePh")}
                className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none"
              />
              <label className="flex items-center justify-between gap-2 text-sm text-slate-300">
                <span>{t("routes.startTime")}</span>
                <input
                  type="time"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                  className="rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none"
                />
              </label>
              {/* color picker */}
              <div className="space-y-1.5">
                <span className="block text-sm text-slate-300">{t("routes.color")}</span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {ROUTE_PALETTE.map((c) => (
                    <button
                      key={c}
                      type="button"
                      onClick={() => setColor(c)}
                      title={c}
                      aria-label={c}
                      className={"h-6 w-6 rounded-full border transition " + (color.toLowerCase() === c.toLowerCase() ? "border-white ring-2 ring-white" : "border-ink-700 hover:scale-110")}
                      style={{ background: c }}
                    />
                  ))}
                  <label className="relative h-6 w-6 cursor-pointer overflow-hidden rounded-full border border-ink-700" title={t("routes.customColor")}>
                    <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="absolute inset-0 h-full w-full cursor-pointer opacity-0" />
                    <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[11px] text-white" style={{ background: color }}>✎</span>
                  </label>
                </div>
              </div>
            </section>

            {/* Section 2 — totals */}
            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("routes.totals")}</h3>
              <div className="flex gap-2 text-xs">
                <div className="flex-1 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2">
                  <div className="text-slate-500">{t("routes.totalKm")}</div>
                  <div className="text-sm font-semibold text-white">{routing ? t("routes.routing") : dir ? dir.km : "—"}</div>
                </div>
                <div className="flex-1 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2">
                  <div className="text-slate-500">{t("routes.totalTime")}</div>
                  <div className="text-sm font-semibold text-white">{routing ? t("routes.routing") : dir ? fmtDur(dir.minutes) : "—"}</div>
                </div>
              </div>
            </section>

            {/* Section 3 — stops */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("routes.stops")} ({stops.length})</h3>
                {stops.length > 1 && <span className="text-[10px] text-slate-600">{t("routes.dragToReorder")}</span>}
              </div>

              <div className="relative">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={hasGoogleKey() ? t("routes.searchGoogle") : t("routes.googleMissing")}
                  disabled={!hasGoogleKey()}
                  className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none disabled:opacity-60"
                />
                {suggestions.length > 0 && (
                  <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-ink-700 bg-ink-900 shadow-xl">
                    {suggestions.map((s) => (
                      <li key={s.placeId}>
                        <button onClick={() => pickSuggestion(s)} className="block w-full px-3 py-2 text-start text-xs text-slate-200 hover:bg-ink-800">{s.description}</button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {searchErr && <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-300">{searchErrMsg(searchErr)}</p>}
              {!searchErr && searching && query.trim() && <p className="px-1 text-[11px] text-slate-500">{t("common.loading")}</p>}
              {!searchErr && !searching && query.trim() && suggestions.length === 0 && <p className="px-1 text-[11px] text-slate-500">{t("routes.search.noResults")}</p>}

              {stops.length === 0 && (
                <div className="rounded-lg border border-dashed border-ink-700 px-3 py-6 text-center text-xs text-slate-500">{t("routes.noStopsYet")}</div>
              )}

              <ol className="space-y-0">
                {stops.map((s, i) => {
                  const next = stops[i + 1];
                  const badge = next ? diffBadge(s.arrival, next.arrival) : null;
                  const selected = s.id === selectedId;
                  return (
                    <li key={s.id} ref={(el) => { listRefs.current.set(s.id, el); }}>
                      <div
                        draggable
                        onDragStart={() => (dragIndexRef.current = i)}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => onListDrop(i)}
                        onClick={() => setSelectedId(s.id)}
                        className={"rounded-lg border p-2 transition-colors " + (selected ? "border-brand bg-brand/10 ring-1 ring-brand/40" : "border-ink-800 hover:border-ink-700")}
                      >
                        <div className="flex items-center gap-2">
                          <span className="flex h-5 w-5 shrink-0 cursor-grab items-center justify-center rounded-full text-[10px] font-bold text-white" style={{ background: color }} title={t("routes.dragToReorder")}>{i + 1}</span>
                          <input
                            value={s.name}
                            onChange={(e) => updateStop(s.id, { name: e.target.value })}
                            placeholder={t("routes.stopNamePh")}
                            className="min-w-0 flex-1 rounded-md border border-ink-700 bg-ink-850 px-2 py-1 text-xs text-slate-100 focus:border-brand focus:outline-none"
                          />
                          <button onClick={(e) => { e.stopPropagation(); removeStop(s.id); }} className="shrink-0 rounded-md border border-red-500/40 px-1.5 py-1 text-[10px] text-red-300 hover:bg-red-500/10" title={t("routes.remove")}>✕</button>
                        </div>
                        <div className="mt-1.5 flex items-center gap-3 ps-7 text-[11px] text-slate-500">
                          <label className="flex items-center gap-1">
                            {t("routes.arrival")}
                            <input type="time" value={s.arrival} onChange={(e) => updateStop(s.id, { arrival: e.target.value })} className="rounded-md border border-ink-700 bg-ink-850 px-1.5 py-0.5 text-[11px] text-slate-100 focus:border-brand focus:outline-none" />
                          </label>
                          <label className="flex items-center gap-1">
                            {t("routes.dwellMin")}
                            <input type="number" min={0} value={s.dwell} onChange={(e) => updateStop(s.id, { dwell: Number(e.target.value) || 0 })} className="w-14 rounded-md border border-ink-700 bg-ink-850 px-1.5 py-0.5 text-[11px] text-slate-100 focus:border-brand focus:outline-none" />
                          </label>
                        </div>
                      </div>
                      {badge && (
                        <div className="flex items-center gap-2 py-1 ps-2.5 text-[10px]">
                          <span className="text-slate-600">⋮</span>
                          <span className={"rounded-full border px-2 py-0.5 " + (badge.invalid ? "border-red-500/40 bg-red-500/10 text-red-300" : "border-ink-700 text-slate-400")}>↓ {badge.text}</span>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>

              {undo && (
                <div className="flex items-center justify-between rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-slate-300">
                  <span>{t("routes.removed")}</span>
                  <button onClick={doUndo} className="font-medium text-brand-sage hover:underline">{t("routes.undo")}</button>
                </div>
              )}
            </section>
          </div>

          {/* Floating footer */}
          <div className="space-y-2 border-t border-ink-800 bg-ink-900 px-4 py-3 shadow-[0_-8px_20px_-8px_rgba(0,0,0,0.6)]">
            {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}
            <div className="flex justify-end gap-2">
              <button onClick={onClose} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button onClick={save} loading={saving} className="w-auto px-6">{editing ? t("common.save") : t("common.create")}</Button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

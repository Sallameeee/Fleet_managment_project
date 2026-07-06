"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import {
  getHistory,
  listDrivers,
  listVehicles,
  type HistoryTrip,
  type ManagerDriver,
  type ManagerVehicle,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import MapView, { type MapboxMap } from "@/components/MapView";

const ACTUAL = "#22c55e"; // green — the path actually driven
const PLANNED = "#3b82f6"; // blue — the assigned route geometry

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function fmtClock(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function pingMs(iso: string): number {
  return new Date(iso.replace(" ", "T")).getTime();
}
function fmtClockIso(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
// Waiting duration, e.g. "4m 12s" (or "48s" under a minute).
function fmtDwell(sec: number | null): string {
  if (sec === null || sec === undefined || sec < 0) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m === 0 ? `${s}s` : `${m}m ${s}s`;
}

export default function ManagerHistoryPage() {
  const { t } = useT();
  const toast = useToast();

  const [kind, setKind] = useState<"drivers" | "vehicles">("drivers");
  const [subjectId, setSubjectId] = useState("");
  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const [trips, setTrips] = useState<HistoryTrip[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // scrubber
  const [scrub, setScrub] = useState(0); // ms offset from trip start

  const mapRef = useRef<MapboxMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const markerRef = useRef<mapboxgl.Marker | null>(null);
  const selectedRef = useRef<HistoryTrip | null>(null);

  useEffect(() => {
    listDrivers().then(setDrivers).catch(() => {});
    listVehicles().then(setVehicles).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setSelectedId(null);
    try {
      const list = await getHistory({ kind, subject_id: subjectId || undefined, date_from: from || undefined, date_to: to || undefined });
      setTrips(list);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setLoading(false);
    }
  }, [kind, subjectId, from, to, toast, t]);

  const selected = useMemo(() => trips.find((x) => x.trip_id === selectedId) ?? null, [trips, selectedId]);
  selectedRef.current = selected;

  // ping timestamps for the selected trip
  const times = useMemo(() => (selected ? selected.pings.map((p) => pingMs(p.recorded_at)) : []), [selected]);
  const t0 = times[0] ?? 0;
  const t1 = times[times.length - 1] ?? 0;
  const duration = Math.max(0, t1 - t0);

  // ---- map drawing ----------------------------------------------------------
  function ensureMarker(map: MapboxMap): mapboxgl.Marker {
    if (!markerRef.current) {
      const el = document.createElement("div");
      el.style.cssText =
        `width:20px;height:20px;border-radius:9999px;background:${ACTUAL};border:3px solid #fff;box-shadow:0 0 0 3px rgba(34,197,94,.4),0 1px 4px rgba(0,0,0,.5)`;
      markerRef.current = new mapboxgl.Marker({ element: el });
    }
    return markerRef.current;
  }

  const drawTrip = useCallback((map: MapboxMap, trip: HistoryTrip | null) => {
    ["hist-actual", "hist-planned"].forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    });
    if (markerRef.current) {
      markerRef.current.remove();
      markerRef.current = null;
    }
    if (!trip) return;
    const bounds = new mapboxgl.LngLatBounds();

    const geo = trip.route_geometry;
    if (geo && Array.isArray(geo.coordinates) && geo.coordinates.length > 1) {
      map.addSource("hist-planned", { type: "geojson", data: { type: "Feature", geometry: geo, properties: {} } as GeoJSON.Feature });
      map.addLayer({
        id: "hist-planned",
        type: "line",
        source: "hist-planned",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": PLANNED, "line-width": 4, "line-opacity": 0.65, "line-dasharray": [2, 1] },
      });
      geo.coordinates.forEach((c) => bounds.extend(c as [number, number]));
    }

    const coords = trip.pings.map((p) => [p.lng, p.lat] as [number, number]);
    if (coords.length > 1) {
      map.addSource("hist-actual", { type: "geojson", data: { type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: {} } as GeoJSON.Feature });
      map.addLayer({
        id: "hist-actual",
        type: "line",
        source: "hist-actual",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ACTUAL, "line-width": 4, "line-opacity": 0.9 },
      });
      coords.forEach((c) => bounds.extend(c));
    }
    if (coords.length) {
      ensureMarker(map).setLngLat(coords[0]).addTo(map);
    }
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 60, maxZoom: 15, duration: 400 });
  }, []);

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    setMapReady(true);
  }
  function handleStyleChange(map: MapboxMap) {
    markerRef.current = null; // setStyle wiped layers; marker recreated by drawTrip
    drawTrip(map, selectedRef.current);
  }

  // redraw + reset scrubber whenever the selected trip changes
  useEffect(() => {
    const map = mapRef.current;
    if (map && mapReady) drawTrip(map, selected);
    setScrub(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, mapReady]);

  // move the driver marker as the scrubber moves
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady || !selected || times.length === 0 || !markerRef.current) return;
    const target = t0 + scrub;
    let lng: number, lat: number;
    if (target <= times[0]) {
      lng = selected.pings[0].lng; lat = selected.pings[0].lat;
    } else if (target >= times[times.length - 1]) {
      const p = selected.pings[selected.pings.length - 1]; lng = p.lng; lat = p.lat;
    } else {
      let i = 0;
      while (i < times.length - 1 && times[i + 1] < target) i++;
      const span = times[i + 1] - times[i] || 1;
      const f = (target - times[i]) / span;
      lng = selected.pings[i].lng + (selected.pings[i + 1].lng - selected.pings[i].lng) * f;
      lat = selected.pings[i].lat + (selected.pings[i + 1].lat - selected.pings[i].lat) * f;
    }
    markerRef.current.setLngLat([lng, lat]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrub, selectedId, mapReady]);

  // ---- grouping -------------------------------------------------------------
  const groups = useMemo(() => {
    const m = new Map<string, HistoryTrip[]>();
    for (const trip of trips) {
      const label = (kind === "drivers" ? trip.driver_name : trip.vehicle_bus_number) ?? "—";
      if (!m.has(label)) m.set(label, []);
      m.get(label)!.push(trip);
    }
    return [...m.entries()];
  }, [trips, kind]);

  const subjectOptions = kind === "drivers"
    ? drivers.map((d) => ({ id: d.id, label: d.name }))
    : vehicles.map((v) => ({ id: v.id, label: v.bus_number }));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.history")}</h1>
        <p className="text-sm text-slate-400">{t("hist.subtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(320px,380px)_1fr]">
        {/* Controls + trip list */}
        <div className="space-y-3">
          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
            <div className="mb-2 inline-flex rounded-lg border border-ink-700 p-0.5">
              {(["drivers", "vehicles"] as const).map((k) => (
                <button key={k} onClick={() => { setKind(k); setSubjectId(""); }} className={"rounded-md px-3 py-1 text-xs font-medium transition-colors " + (kind === k ? "bg-brand text-white" : "text-slate-400 hover:text-white")}>
                  {k === "drivers" ? t("hist.drivers") : t("hist.vehicles")}
                </button>
              ))}
            </div>
            <select value={subjectId} onChange={(e) => setSubjectId(e.target.value)} className="mb-2 w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none">
              <option value="">{t("hist.all")} {kind === "drivers" ? t("hist.drivers") : t("hist.vehicles")}</option>
              {subjectOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            <div className="mb-2 grid grid-cols-2 gap-2">
              <label className="block"><span className="mb-1 block text-xs text-slate-400">{t("common.from")}</span>
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100" /></label>
              <label className="block"><span className="mb-1 block text-xs text-slate-400">{t("common.to")}</span>
                <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100" /></label>
            </div>
            <button onClick={load} disabled={loading} className="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand-sage disabled:opacity-60">
              {loading ? t("common.loading") : t("hist.load")}
            </button>
          </div>

          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-2">
            {!loading && trips.length === 0 && <div className="px-2 py-6 text-center text-sm text-slate-500">{t("hist.noTrips")}</div>}
            <div className="max-h-[520px] space-y-3 overflow-y-auto">
              {groups.map(([label, list]) => (
                <div key={label}>
                  <div className="sticky top-0 bg-ink-900/90 px-2 py-1 text-xs font-semibold uppercase tracking-wide text-slate-400 backdrop-blur">
                    {label} · {list.length} {t("hist.trips")}
                  </div>
                  <ul className="mt-1 space-y-1">
                    {list.map((trip) => {
                      const sel = trip.trip_id === selectedId;
                      return (
                        <li key={trip.trip_id}>
                          <button onClick={() => setSelectedId(trip.trip_id)} className={"w-full rounded-lg border px-3 py-2 text-start text-sm transition-colors " + (sel ? "border-brand bg-brand/10" : "border-ink-800 hover:border-ink-700")}>
                            <div className="flex items-center justify-between gap-2">
                              <span className="truncate font-medium text-white">{trip.route_name ?? "—"}</span>
                              <span className="shrink-0 text-xs text-slate-500">{trip.vehicle_bus_number ?? "—"}</span>
                            </div>
                            <div className="mt-0.5 text-xs text-slate-400">
                              {fmtTime(trip.started_at)} → {trip.ended_at ? fmtTime(trip.ended_at) : t("hist.ongoing")}
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Map + scrubber */}
        <div className="space-y-3">
          <div dir="ltr" className="relative">
            <div className="h-[440px] overflow-hidden rounded-xl border border-ink-800">
              <MapView className="h-full w-full" styleSwitcher onReady={handleReady} onStyleChange={handleStyleChange} />
            </div>
            {!selected && (
              <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                <span className="rounded-lg bg-ink-900/80 px-4 py-2 text-sm text-slate-300 backdrop-blur">{t("hist.selectTrip")}</span>
              </div>
            )}
            {/* legend */}
            {selected && (
              <div className="absolute bottom-3 left-3 z-10 rounded-lg border border-ink-700 bg-ink-900/90 p-2 text-xs backdrop-blur">
                <div className="flex items-center gap-2"><span className="h-1 w-5 rounded" style={{ background: ACTUAL }} />{t("hist.actualPath")}</div>
                <div className="mt-1 flex items-center gap-2"><span className="h-1 w-5 rounded" style={{ background: PLANNED }} />{t("hist.plannedRoute")}</div>
              </div>
            )}
          </div>

          {/* Timeline scrubber */}
          {selected && (
            <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
              {duration > 0 ? (
                <>
                  <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                    <span>{fmtClock(t0)}</span>
                    <span className="font-semibold text-brand-sage">{fmtClock(t0 + scrub)}</span>
                    <span>{fmtClock(t1)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={duration}
                    value={scrub}
                    onChange={(e) => setScrub(Number(e.target.value))}
                    title={t("hist.scrubHint")}
                    className="w-full accent-[#3AA76D]"
                  />
                </>
              ) : (
                <p className="text-center text-xs text-slate-500">{t("hist.noPath")}</p>
              )}
            </div>
          )}

          {/* Per-stop waiting times (from stop_visits) */}
          {selected && selected.stop_visits.length > 0 && (
            <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{t("hist.stopsTitle")}</h3>
              <ol className="space-y-2">
                {selected.stop_visits.map((v, i) => {
                  const planned = v.planned_dwell_seconds;
                  return (
                    <li key={v.stop_id ?? i} className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white" style={{ background: selected.route_color || ACTUAL }}>
                        {v.stop_order ?? i + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium text-white">{v.stop_name ?? "—"}</span>
                          <span className="shrink-0 text-xs text-slate-400">{t("hist.arrived")} {fmtClockIso(v.arrival_time)}</span>
                        </div>
                        <div className="mt-0.5 text-xs text-slate-400">
                          {v.departure_time ? (
                            <>
                              {t("hist.waited")} <span className="font-semibold text-brand-sage">{fmtDwell(v.actual_dwell_seconds)}</span>
                              {planned != null && planned > 0 && (
                                <span className="text-slate-500"> · {fmtDwell(planned)} {t("hist.planned")}</span>
                              )}
                            </>
                          ) : (
                            <span className="text-amber-300/80">{t("hist.stillThere")}</span>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </div>
          )}
          {selected && selected.stop_visits.length === 0 && (
            <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3 text-center text-xs text-slate-500">
              {t("hist.noStopVisits")}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

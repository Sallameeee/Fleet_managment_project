"use client";

// TRIP DEBUG — admin-only view of the trip decision log (backend /trip-debug).
// Answers "why no path / why OFFLINE / why the wrong next stop?" from evidence:
// diagnosis, every fix (green = accepted, red = rejected, tap for the reason),
// the timeline of server + app decisions, stop progress, export JSON/CSV.
// Times are shown in Africa/Cairo (stored UTC). Data is the caller's org only.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import MapView, { type MapboxMap } from "@/components/MapView";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import {
  downloadTripDebug,
  getTripDebug,
  listDebugTrips,
  type TripDebug,
  type TripDebugEvent,
  type TripDebugFix,
  type TripDebugListItem,
} from "@/lib/manager";

const GREEN = "#22c55e";
const RED = "#ef4444";
const BLUE = "#3b82f6";
const AMBER = "#f59e0b";
const TZ = "Africa/Cairo";

const STATE_STYLE: Record<string, string> = {
  online: "bg-green-500/15 text-green-400 ring-green-500/30",
  online_no_gps: "bg-amber-500/15 text-amber-400 ring-amber-500/30",
  offline: "bg-slate-500/15 text-slate-400 ring-slate-500/30",
};

function cairo(iso: string | null | undefined, withDate = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-GB", {
    timeZone: TZ,
    ...(withDate ? { day: "2-digit", month: "short" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function cairoDate(d: Date): string {
  // YYYY-MM-DD of `d` in Cairo time (for the date inputs).
  return d.toLocaleDateString("en-CA", { timeZone: TZ });
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

function fixPopup(f: TripDebugFix, t: (k: string) => string): string {
  const rows: [string, string][] = [
    ["time", cairo(f.recorded_at, true)],
    ["verdict", f.accepted ? "accepted" : `rejected: ${f.reason ?? "?"}`],
  ];
  if (!f.accepted && f.reason) {
    const help = t(`td.reasonHelp.${f.reason}`);
    if (!help.startsWith("td.")) rows.push(["why", help]);
  }
  if (f.implied_kmh != null) rows.push(["implied", `${f.implied_kmh} km/h`]);
  if (f.speed != null) rows.push(["device speed", `${Math.round(f.speed * 3.6)} km/h`]);
  if (f.accuracy != null) rows.push(["accuracy", `${Math.round(f.accuracy)} m`]);
  if (f.lag_s != null) rows.push(["upload lag", `${f.lag_s} s`]);
  if (f.net_state) rows.push(["network", f.net_state]);
  if (f.battery != null) rows.push(["battery", `${f.battery} %`]);
  return (
    `<div style="font:12px/1.4 system-ui;color:#0f172a;min-width:180px">` +
    rows.map(([k, v]) => `<div><b>${esc(k)}</b>: ${esc(v)}</div>`).join("") +
    `</div>`
  );
}

export default function TripDebugPage() {
  const { t } = useT();
  const toast = useToast();
  const today = useMemo(() => cairoDate(new Date()), []);
  const weekAgo = useMemo(() => cairoDate(new Date(Date.now() - 7 * 86400000)), []);
  const [from, setFrom] = useState(weekAgo);
  const [to, setTo] = useState(today);
  const [trips, setTrips] = useState<TripDebugListItem[]>([]);
  const [tripId, setTripId] = useState<string>("");
  const [data, setData] = useState<TripDebug | null>(null);
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState<"" | "app" | "server">("");
  const [typeFilter, setTypeFilter] = useState("");
  const [limit, setLimit] = useState(300);
  const [open, setOpen] = useState<number | null>(null);
  const mapRef = useRef<MapboxMap | null>(null);
  const popupRef = useRef<mapboxgl.Popup | null>(null);

  // ?trip=<id> deep link (from History).
  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("trip");
    if (q) setTripId(q);
  }, []);

  useEffect(() => {
    listDebugTrips(from, to)
      .then(setTrips)
      .catch((e: Error) => toast.error(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [from, to]);

  useEffect(() => {
    if (!tripId) return;
    setLoading(true);
    setData(null);
    getTripDebug(tripId)
      .then((d) => {
        setData(d);
        const u = new URL(window.location.href);
        u.searchParams.set("trip", tripId);
        window.history.replaceState(null, "", u.toString());
      })
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tripId]);

  const draw = useCallback(
    (map: MapboxMap) => {
      if (!data || !map.isStyleLoaded()) return;
      const acc = data.fixes.filter((f) => f.accepted);
      const rej = data.fixes.filter((f) => !f.accepted);
      const pointFc = (list: TripDebugFix[]) => ({
        type: "FeatureCollection" as const,
        features: list.map((f) => ({
          type: "Feature" as const,
          properties: { id: f.id },
          geometry: { type: "Point" as const, coordinates: [f.lng, f.lat] },
        })),
      });
      const sources: Record<string, GeoJSON.FeatureCollection> = {
        "td-path": {
          type: "FeatureCollection",
          features: acc.length > 1 ? [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: acc.map((f) => [f.lng, f.lat]) } }] : [],
        },
        "td-acc": pointFc(acc),
        "td-rej": pointFc(rej),
        "td-stops": {
          type: "FeatureCollection",
          features: data.stops.map((s) => ({
            type: "Feature",
            properties: { label: `${s.stop_order}. ${s.name ?? ""}` },
            geometry: { type: "Point", coordinates: [s.lng, s.lat] },
          })),
        },
      };
      for (const [id, fc] of Object.entries(sources)) {
        const src = map.getSource(id) as mapboxgl.GeoJSONSource | undefined;
        if (src) src.setData(fc);
        else map.addSource(id, { type: "geojson", data: fc });
      }
      const add = (layer: mapboxgl.AnyLayer) => {
        if (!map.getLayer(layer.id)) map.addLayer(layer);
      };
      add({ id: "td-path", type: "line", source: "td-path", paint: { "line-color": GREEN, "line-width": 3, "line-opacity": 0.6 } });
      add({ id: "td-acc", type: "circle", source: "td-acc", paint: { "circle-color": GREEN, "circle-radius": 3.5, "circle-stroke-color": "#052e16", "circle-stroke-width": 0.5 } });
      add({ id: "td-rej", type: "circle", source: "td-rej", paint: { "circle-color": RED, "circle-radius": 6, "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } });
      add({ id: "td-stops", type: "circle", source: "td-stops", paint: { "circle-color": BLUE, "circle-radius": 7, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      add({
        id: "td-stop-labels",
        type: "symbol",
        source: "td-stops",
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.3], "text-anchor": "top" },
        paint: { "text-color": BLUE, "text-halo-color": "#fff", "text-halo-width": 1 },
      });
      const pts = [...data.fixes.map((f) => [f.lng, f.lat] as [number, number]), ...data.stops.map((s) => [s.lng, s.lat] as [number, number])];
      if (pts.length) {
        const b = new mapboxgl.LngLatBounds(pts[0], pts[0]);
        pts.forEach((p) => b.extend(p));
        map.fitBounds(b, { padding: 40, maxZoom: 15, duration: 0 });
      }
    },
    [data],
  );

  useEffect(() => {
    if (mapRef.current) draw(mapRef.current);
  }, [draw]);

  const handleReady = useCallback(
    (map: MapboxMap) => {
      mapRef.current = map;
      const byId = () => new Map((data?.fixes ?? []).map((f) => [f.id, f]));
      const onClick = (e: mapboxgl.MapLayerMouseEvent) => {
        // A rejected fix drawn over an accepted one wins the tap.
        const onRed = map.queryRenderedFeatures(e.point, { layers: ["td-rej"] });
        if (e.features?.[0]?.layer?.id === "td-acc" && onRed.length) return;
        const id = e.features?.[0]?.properties?.id as number | undefined;
        const f = id != null ? byId().get(Number(id)) : undefined;
        if (!f) return;
        popupRef.current?.remove();
        popupRef.current = new mapboxgl.Popup({ closeButton: true }).setLngLat([f.lng, f.lat]).setHTML(fixPopup(f, t)).addTo(map);
      };
      map.on("click", "td-rej", onClick);
      map.on("click", "td-acc", onClick);
      map.on("mouseenter", "td-rej", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "td-rej", () => (map.getCanvas().style.cursor = ""));
      draw(map);
    },
    // Re-bound when data changes so the popup finds the current fixes.
    [data, draw, t],
  );

  const events = useMemo(() => {
    const q = typeFilter.trim().toLowerCase();
    return (data?.events ?? []).filter(
      (e) => (!source || e.source === source) && (!q || e.type.toLowerCase().includes(q) || e.message.toLowerCase().includes(q)),
    );
  }, [data, source, typeFilter]);

  const progressRows = data?.progress?.stops ?? [];

  async function exportAs(fmt: "json" | "csv") {
    if (!tripId) return;
    try {
      await downloadTripDebug(tripId, fmt);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">{t("td.title")}</h1>
        <p className="text-sm text-slate-400">
          {t("td.subtitle")} {t("td.times")}
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3 rounded-xl border border-white/10 p-3">
        <label className="text-xs text-slate-400">
          {t("td.from")}
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="mt-1 block rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm" />
        </label>
        <label className="text-xs text-slate-400">
          {t("td.to")}
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="mt-1 block rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm" />
        </label>
        <label className="min-w-0 flex-1 basis-full text-xs text-slate-400 sm:basis-auto">
          {t("td.pickTrip")}
          <select value={tripId} onChange={(e) => setTripId(e.target.value)} className="mt-1 block w-full rounded-md border border-white/10 bg-transparent px-2 py-1.5 text-sm">
            <option value="">{trips.length ? "—" : t("td.noTrips")}</option>
            {trips.map((tr) => (
              <option key={tr.id} value={tr.id}>
                {cairo(tr.started_at, true)} · {tr.route_name ?? "—"} · {tr.vehicle_bus_number ?? "—"} · {tr.status} · {tr.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <div className="flex gap-2">
          <button type="button" disabled={!data} onClick={() => exportAs("json")} className="rounded-md border border-white/10 px-3 py-1.5 text-sm disabled:opacity-40">
            {t("td.exportJson")}
          </button>
          <button type="button" disabled={!data} onClick={() => exportAs("csv")} className="rounded-md border border-white/10 px-3 py-1.5 text-sm disabled:opacity-40">
            {t("td.exportCsv")}
          </button>
        </div>
      </div>

      {!tripId && <p className="text-sm text-slate-400">{t("td.choose")}</p>}
      {loading && <p className="text-sm text-slate-400">{t("common.loading")}</p>}

      {data && (
        <>
          {!data.log_available && <p className="rounded-lg bg-amber-500/10 p-3 text-sm text-amber-300">{t("td.noLog")}</p>}

          <section className="rounded-xl border border-white/10 p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">{t("td.diagnosis")}</h2>
            <ul className="list-disc space-y-1 ps-5 text-sm">
              {data.diagnosis.map((d, i) => (
                <li key={i}>{d}</li>
              ))}
            </ul>
          </section>

          <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label={t("td.fixes")} value={`${data.counts.accepted} / ${data.counts.fixes}`} hint={`${t("td.accepted")} / total`} />
            <Stat label={t("td.heartbeats")} value={String(data.counts.heartbeats)} hint={data.heartbeats.gaps.length ? `${data.heartbeats.gaps.length} ${t("td.hbGaps").toLowerCase()}` : ""} />
            <Stat label={t("td.events")} value={String(data.counts.events)} hint={Object.entries(data.counts.events_by_source).map(([k, v]) => `${k}: ${v}`).join(" · ")} />
            <div className="rounded-xl border border-white/10 p-3">
              <div className="text-xs text-slate-400">{t("td.liveNow")}</div>
              <span className={"mt-1 inline-block rounded-full px-2 py-0.5 text-sm ring-1 " + (STATE_STYLE[data.live.state] ?? "")}>{t(`td.state.${data.live.state}`)}</span>
              <div className="mt-1 text-xs text-slate-500">{data.live.reason}</div>
            </div>
          </section>

          <section className="rounded-xl border border-white/10 p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">{t("td.byReason")}</h2>
            <div className="flex flex-wrap gap-2 text-sm">
              {Object.keys(data.counts.rejected_by_reason).length === 0 && <span className="text-slate-500">{t("td.none")}</span>}
              {Object.entries(data.counts.rejected_by_reason).map(([r, n]) => (
                <span key={r} className="rounded-full bg-red-500/15 px-2 py-0.5 text-red-300 ring-1 ring-red-500/30" title={t(`td.reasonHelp.${r}`)}>
                  {r} × {n}
                </span>
              ))}
            </div>
          </section>

          <section className="overflow-hidden rounded-xl border border-white/10">
            <div className="flex flex-wrap gap-4 px-3 py-2 text-xs text-slate-400">
              <Legend color={GREEN} label={t("td.legendAccepted")} />
              <Legend color={RED} label={t("td.legendRejected")} />
              <Legend color={BLUE} label={t("td.legendStop")} />
            </div>
            <div className="h-[420px]">
              <MapView key={tripId} className="h-full w-full" styleSwitcher onReady={handleReady} onStyleChange={draw} />
            </div>
          </section>

          {data.progress && (
            <section className="rounded-xl border border-white/10 p-4">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-400">
                {t("td.stops")} · {data.progress.done + data.progress.skipped}/{data.progress.total} · {t("td.radius")} {data.progress.arrive_radius_m} m
              </h2>
              {data.progress.next_stop && (
                <p className="mb-2 text-sm">
                  {t("td.nextStop")}: <b>{data.progress.next_stop.stop_order}. {data.progress.next_stop.name}</b>
                  {data.progress.next_stop.distance_m != null && ` · ${Math.round(data.progress.next_stop.distance_m)} m`}
                </p>
              )}
              <ol className="space-y-1 text-sm">
                {progressRows.map((s) => (
                  <li key={s.stop_id} className="flex items-center gap-2">
                    <span className="w-6 text-end text-slate-500">{s.stop_order}.</span>
                    <span className="flex-1 truncate">{s.name}</span>
                    <span
                      className={
                        "rounded-full px-2 py-0.5 text-xs " +
                        (s.status === "visited" ? "bg-green-500/15 text-green-400" : s.status === "passed" ? "bg-sky-500/15 text-sky-300" : s.status === "skipped" ? "bg-red-500/15 text-red-300" : "bg-slate-500/15 text-slate-400")
                      }
                    >
                      {t(`td.st.${s.status ?? "pending"}`)}
                    </span>
                    {s.closest_m != null && <span className="w-28 text-end text-xs text-slate-500">{t("td.closest")} {Math.round(s.closest_m)} m</span>}
                  </li>
                ))}
              </ol>
            </section>
          )}

          <section className="rounded-xl border border-white/10 p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="me-auto text-sm font-semibold uppercase tracking-wide text-slate-400">
                {t("td.timeline")} ({events.length})
              </h2>
              <select value={source} onChange={(e) => setSource(e.target.value as "" | "app" | "server")} className="rounded-md border border-white/10 bg-transparent px-2 py-1 text-sm">
                <option value="">{t("td.allSources")}</option>
                <option value="server">{t("td.source.server")}</option>
                <option value="app">{t("td.source.app")}</option>
              </select>
              <input value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} placeholder={t("td.filterType")} className="w-44 rounded-md border border-white/10 bg-transparent px-2 py-1 text-sm" />
            </div>
            <ul className="divide-y divide-white/5 text-sm">
              {events.slice(0, limit).map((e: TripDebugEvent) => (
                <li key={e.id} className="py-1.5">
                  <button type="button" onClick={() => setOpen(open === e.id ? null : e.id)} className="flex w-full items-start gap-2 text-start">
                    <span className="w-16 shrink-0 font-mono text-xs text-slate-500">{cairo(e.occurred_at)}</span>
                    <span className={"shrink-0 rounded px-1.5 text-[10px] uppercase " + (e.source === "app" ? "bg-violet-500/20 text-violet-300" : "bg-slate-500/20 text-slate-300")}>{e.source}</span>
                    <span className="shrink-0 font-mono text-xs" style={{ color: e.type.includes("reject") || e.type.includes("skipped") ? RED : e.type === "live_state" ? AMBER : undefined }}>
                      {e.type}
                    </span>
                    <span className="min-w-0 flex-1 break-words">{e.message}</span>
                  </button>
                  {open === e.id && (
                    <pre className="mt-1 max-h-64 overflow-auto rounded bg-black/30 p-2 text-xs" dir="ltr">
                      {JSON.stringify(e.payload, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
            {events.length > limit && (
              <button type="button" onClick={() => setLimit(limit + 300)} className="mt-2 text-sm text-sky-400">
                {t("td.showMore")}
              </button>
            )}
            <p className="mt-3 text-xs text-slate-500">{t("td.retention").replace("{n}", String(data.retention_days))}</p>
          </section>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-white/10 p-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
      {hint && <div className="mt-0.5 truncate text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

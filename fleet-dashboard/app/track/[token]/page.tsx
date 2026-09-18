"use client";

/**
 * PUBLIC passenger tracking page — the destination of a vehicle's
 * "passenger tracking link" (Vehicles page → Copy). No login: anyone with the
 * link sees the live position of whatever trip is running on that bus, via the
 * backend's only unauthenticated endpoint (GET /track/{share_token}), which
 * returns a whitelisted minimum (bus number, position, route stops) and nothing
 * about the org or the driver. Polls every 10 s. Works for school and
 * university vehicles alike — the endpoint is module-agnostic.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import mapboxgl from "mapbox-gl";
import MapView, { type MapboxMap } from "@/components/MapView";
import { useT } from "@/lib/i18n";
import { DEFAULT_ROUTE_COLOR } from "@/lib/mapbox";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const POLL_MS = 10_000;

type Track =
  | { status: "live"; bus_number: string; position: { lat: number; lng: number; recorded_at: string } | null; route: { name: string | null; stops: { name: string; lat: number; lng: number; order: number }[] } | null }
  | { status: "not_in_service" }
  | { status: "outside_hours"; resumes_at: string }
  | { status: "not_found" }
  | { status: "error" };

function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export default function PublicTrackPage() {
  const { t } = useT();
  const params = useParams<{ token: string }>();
  const token = params?.token ?? "";
  const [track, setTrack] = useState<Track | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const mapRef = useRef<MapboxMap | null>(null);
  const busRef = useRef<mapboxgl.Marker | null>(null);
  const stopsRef = useRef<mapboxgl.Marker[]>([]);
  const fittedRef = useRef(false);
  const trackRef = useRef<Track | null>(null);
  trackRef.current = track;

  const poll = useCallback(async () => {
    if (!token) return;
    try {
      const res = await fetch(`${API_URL}/track/${encodeURIComponent(token)}`, { cache: "no-store" });
      if (res.status === 404) {
        setTrack({ status: "not_found" });
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      setTrack((await res.json()) as Track);
      setUpdatedAt(new Date());
    } catch {
      setTrack((prev) => prev ?? { status: "error" });
    }
  }, [token]);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  // ---- map drawing: numbered stop markers + a bus marker that moves in place ----
  const draw = useCallback((map: MapboxMap, tr: Track | null) => {
    if (!tr || tr.status !== "live") return;
    const stops = tr.route?.stops ?? [];
    if (stopsRef.current.length !== stops.length) {
      stopsRef.current.forEach((m) => m.remove());
      stopsRef.current = stops.map((s, i) => {
        const el = document.createElement("div");
        el.textContent = String(i + 1);
        el.title = s.name;
        el.style.cssText =
          `width:24px;height:24px;border-radius:9999px;background:${DEFAULT_ROUTE_COLOR};color:#fff;display:flex;align-items:center;` +
          `justify-content:center;font-size:11px;font-weight:700;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4)`;
        return new mapboxgl.Marker({ element: el }).setLngLat([s.lng, s.lat]).addTo(map);
      });
    }
    if (tr.position) {
      if (!busRef.current) {
        const el = document.createElement("div");
        el.style.cssText =
          "width:22px;height:22px;border-radius:9999px;background:#2563eb;border:3px solid #fff;box-shadow:0 0 0 4px rgba(37,99,235,.35),0 1px 4px rgba(0,0,0,.5)";
        busRef.current = new mapboxgl.Marker({ element: el });
      }
      busRef.current.setLngLat([tr.position.lng, tr.position.lat]).addTo(map);
    }
    if (!fittedRef.current) {
      const b = new mapboxgl.LngLatBounds();
      stops.forEach((s) => b.extend([s.lng, s.lat]));
      if (tr.position) b.extend([tr.position.lng, tr.position.lat]);
      if (!b.isEmpty()) {
        map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 0 });
        fittedRef.current = true;
      }
    }
  }, []);

  useEffect(() => {
    if (mapRef.current) draw(mapRef.current, track);
  }, [track, draw]);

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    draw(map, trackRef.current);
  }
  // Fires on every style load (including the initial one when the theme swap
  // restarts it before `load`), so the map is bound here too — the poll effect
  // below then draws as soon as data exists.
  function handleStyleChange(map: MapboxMap) {
    const first = mapRef.current !== map;
    mapRef.current = map;
    busRef.current = null;
    stopsRef.current = [];
    if (!first) fittedRef.current = true; // keep the viewer's viewport across a later style swap
    draw(map, trackRef.current);
  }

  const live = track?.status === "live" ? track : null;
  let headline = t("track.loading");
  let sub = "";
  if (track?.status === "not_found") headline = t("track.invalid");
  else if (track?.status === "error") headline = t("track.unavailable");
  else if (track?.status === "outside_hours") {
    headline = t("track.outsideHours");
    sub = `${t("track.resumesAt")} ${String(track.resumes_at).slice(0, 5)}`;
  } else if (track?.status === "not_in_service") headline = t("track.notInService");
  else if (live) {
    headline = `${t("track.bus")} ${live.bus_number}${live.route?.name ? ` · ${live.route.name}` : ""}`;
    sub = live.position ? `${t("track.lastPosition")} ${fmtClock(live.position.recorded_at)}` : t("track.waitingPosition");
  }

  return (
    <div className="flex h-dvh flex-col bg-ink-950">
      <header className="flex items-center justify-between gap-3 border-b border-ink-800 px-4 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold text-white">{headline}</h1>
          {sub && <p className="truncate text-xs text-slate-400">{sub}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
          {live && live.position && <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-400" aria-hidden />}
          {updatedAt && <span>{t("track.updated")} {updatedAt.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>}
        </div>
      </header>
      {/* The map stays LTR so it never mirrors under RTL */}
      <div dir="ltr" className="relative min-h-0 flex-1">
        <MapView className="h-full w-full" interactive onReady={handleReady} onStyleChange={handleStyleChange} />
        {track && track.status !== "live" && (
          <div className="pointer-events-none absolute inset-x-4 top-4 rounded-xl border border-ink-700 bg-ink-900/90 px-4 py-3 text-center text-sm text-slate-200 backdrop-blur">
            {headline}
            {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
          </div>
        )}
      </div>
      {live && live.route && live.route.stops.length > 0 && (
        <footer className="max-h-[30dvh] overflow-y-auto border-t border-ink-800 px-4 py-2">
          <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-300">
            {live.route.stops.map((s, i) => (
              <li key={`${s.order}-${i}`} className="flex items-center gap-1.5">
                <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-brand text-[10px] font-bold text-white">{i + 1}</span>
                <span className="truncate">{s.name}</span>
              </li>
            ))}
          </ol>
        </footer>
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import {
  listCenters,
  createCenter,
  updateCenter,
  deleteCenter,
  type OrgCenter,
} from "@/lib/manager";
import { reverseGeocode } from "@/lib/mapbox";
import { googleAutocomplete, googlePlaceDetails, hasGoogleKey, type Suggestion } from "@/lib/google";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import MapView, { type MapboxMap } from "@/components/MapView";

export default function CentersManager() {
  const { t } = useT();
  const toast = useToast();
  const [centers, setCenters] = useState<OrgCenter[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [loc, setLoc] = useState<{ lat: number; lng: number } | null>(null);
  const [isPrimary, setIsPrimary] = useState(false);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [saving, setSaving] = useState(false);

  const mapRef = useRef<MapboxMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const centerMarkersRef = useRef<mapboxgl.Marker[]>([]);
  const workMarkerRef = useRef<mapboxgl.Marker | null>(null);
  const locRef = useRef<{ lat: number; lng: number } | null>(loc);
  locRef.current = loc;
  const nameRef = useRef(name);
  nameRef.current = name;

  const reload = useCallback(async () => {
    try {
      setCenters(await listCenters());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    reload();
  }, [reload]);

  // debounced google search
  useEffect(() => {
    if (!query.trim()) {
      setSuggestions([]);
      return;
    }
    const h = window.setTimeout(() => googleAutocomplete(query).then((r) => setSuggestions(r.suggestions)), 250);
    return () => window.clearTimeout(h);
  }, [query]);

  function handleReady(map: MapboxMap) {
    mapRef.current = map;
    map.on("click", (e) => {
      setLoc({ lat: e.lngLat.lat, lng: e.lngLat.lng });
      if (!nameRef.current.trim()) reverseGeocode(e.lngLat.lng, e.lngLat.lat).then((n) => n && setName((cur) => cur || n));
    });
    setMapReady(true);
  }

  // center markers
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    centerMarkersRef.current.forEach((m) => m.remove());
    centerMarkersRef.current = [];
    centers.forEach((c) => {
      const el = document.createElement("div");
      el.textContent = c.is_primary ? "★" : "●";
      el.style.cssText =
        `font-size:${c.is_primary ? 20 : 14}px;color:${c.is_primary ? "#f59e0b" : "#3AA76D"};` +
        "cursor:pointer;text-shadow:0 1px 3px rgba(0,0,0,.6)";
      el.title = c.name;
      centerMarkersRef.current.push(new mapboxgl.Marker({ element: el }).setLngLat([c.lng, c.lat]).addTo(map));
    });
    if (centers.length && !locRef.current) {
      const b = new mapboxgl.LngLatBounds();
      centers.forEach((c) => b.extend([c.lng, c.lat]));
      map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 300 });
    }
  }, [centers, mapReady]);

  // working (pending/edit) marker — draggable
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (!loc) {
      workMarkerRef.current?.remove();
      workMarkerRef.current = null;
      return;
    }
    if (!workMarkerRef.current) {
      const el = document.createElement("div");
      el.style.cssText =
        "width:18px;height:18px;border-radius:9999px;background:#2563eb;border:3px solid #fff;box-shadow:0 0 0 3px rgba(37,99,235,.4),0 1px 4px rgba(0,0,0,.5);cursor:grab";
      const m = new mapboxgl.Marker({ element: el, draggable: true });
      m.on("dragend", () => {
        const ll = m.getLngLat();
        setLoc({ lat: ll.lat, lng: ll.lng });
      });
      workMarkerRef.current = m;
    }
    workMarkerRef.current.setLngLat([loc.lng, loc.lat]).addTo(map);
  }, [loc, mapReady]);

  function resetForm() {
    setEditingId(null);
    setName("");
    setLoc(null);
    setIsPrimary(false);
    setQuery("");
  }

  async function pickSuggestion(s: Suggestion) {
    setSuggestions([]);
    setQuery("");
    const det = await googlePlaceDetails(s.placeId);
    if (!det) return;
    setLoc({ lat: det.lat, lng: det.lng });
    if (!name.trim()) setName(det.name);
    mapRef.current?.flyTo({ center: [det.lng, det.lat], zoom: 15 });
  }

  async function save() {
    if (!name.trim()) {
      toast.error(t("centers.namePh"));
      return;
    }
    if (!loc) {
      toast.error(t("centers.pickFirst"));
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await updateCenter(editingId, { name: name.trim(), lat: loc.lat, lng: loc.lng, is_primary: isPrimary || undefined });
        toast.success(t("toast.saved"));
      } else {
        await createCenter({ name: name.trim(), lat: loc.lat, lng: loc.lng, is_primary: isPrimary });
        toast.success(t("toast.created"));
      }
      resetForm();
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  function edit(c: OrgCenter) {
    setEditingId(c.id);
    setName(c.name);
    setLoc({ lat: c.lat, lng: c.lng });
    setIsPrimary(c.is_primary);
    mapRef.current?.flyTo({ center: [c.lng, c.lat], zoom: 15 });
  }

  async function makePrimary(c: OrgCenter) {
    try {
      await updateCenter(c.id, { is_primary: true });
      toast.success(t("toast.saved"));
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  async function remove(c: OrgCenter) {
    if (!window.confirm(t("centers.deleteConfirm"))) return;
    try {
      await deleteCenter(c.id);
      toast.success(t("toast.deleted"));
      if (editingId === c.id) resetForm();
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
      {/* Map picker */}
      <div dir="ltr" className="relative">
        <div className="h-[420px] overflow-hidden rounded-xl border border-ink-800">
          <MapView className="h-full w-full" onReady={handleReady} />
        </div>
        {/* search */}
        <div className="absolute left-3 top-3 z-10 w-72">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={hasGoogleKey() ? t("full.searchPlace") : t("routes.googleMissing")}
            disabled={!hasGoogleKey()}
            className="w-full rounded-lg border border-ink-700 bg-ink-900/95 px-3 py-2 text-sm text-slate-100 shadow-lg backdrop-blur focus:border-brand focus:outline-none disabled:opacity-60"
          />
          {suggestions.length > 0 && (
            <ul className="mt-1 max-h-56 overflow-auto rounded-lg border border-ink-700 bg-ink-900 shadow-xl">
              {suggestions.map((s) => (
                <li key={s.placeId}>
                  <button onClick={() => pickSuggestion(s)} className="block w-full px-3 py-2 text-start text-xs text-slate-200 hover:bg-ink-800">{s.description}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Form + list */}
      <div className="space-y-3">
        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-3">
          <p className="mb-2 text-xs text-slate-500">{t("centers.searchHint")}</p>
          {editingId && <div className="mb-2 text-xs text-brand-sage">{t("centers.editing")}</div>}
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("centers.namePh")}
            className="mb-2 w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none"
          />
          <div className="mb-2 text-xs text-slate-500">
            {loc ? `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)}` : "—"}
          </div>
          <label className="mb-2 flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={isPrimary} onChange={(e) => setIsPrimary(e.target.checked)} className="h-4 w-4 accent-[#3AA76D]" />
            {t("centers.primary")}
          </label>
          <div className="flex gap-2">
            <Button onClick={save} loading={saving} className="w-auto px-5">{editingId ? t("common.save") : t("centers.add")}</Button>
            {editingId && <button onClick={resetForm} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>}
          </div>
        </div>

        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-2">
          {centers.length === 0 && <div className="px-2 py-4 text-center text-xs text-slate-500">{t("centers.none")}</div>}
          <ul className="space-y-1">
            {centers.map((c) => (
              <li key={c.id} className="flex items-center gap-2 rounded-lg border border-ink-800 px-2.5 py-2 text-sm">
                <span className={c.is_primary ? "text-amber-400" : "text-brand-sage"}>{c.is_primary ? "★" : "●"}</span>
                <button onClick={() => edit(c)} className="min-w-0 flex-1 truncate text-start text-slate-200 hover:text-white">{c.name}</button>
                {c.is_primary ? (
                  <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-300">{t("centers.primary")}</span>
                ) : (
                  <button onClick={() => makePrimary(c)} className="shrink-0 text-[10px] text-slate-400 hover:text-brand-sage">{t("centers.makePrimary")}</button>
                )}
                <button onClick={() => remove(c)} className="shrink-0 rounded-md border border-red-500/40 px-1.5 py-0.5 text-[10px] text-red-300 hover:bg-red-500/10">✕</button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

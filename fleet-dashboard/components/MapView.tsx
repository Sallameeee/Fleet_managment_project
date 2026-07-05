"use client";

import { useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { MAPBOX_TOKEN, hasMapboxToken, CAIRO } from "@/lib/mapbox";
import { useT } from "@/lib/i18n";

// Base-map styles for the optional switcher. "auto" follows the app theme.
const SWITCHER_STYLES: { key: string; url: string | null }[] = [
  { key: "auto", url: null },
  { key: "streets", url: "mapbox://styles/mapbox/streets-v12" },
  { key: "satellite", url: "mapbox://styles/mapbox/satellite-v9" },
  { key: "satelliteStreets", url: "mapbox://styles/mapbox/satellite-streets-v12" },
  { key: "light", url: "mapbox://styles/mapbox/light-v11" },
  { key: "dark", url: "mapbox://styles/mapbox/dark-v11" },
  { key: "outdoors", url: "mapbox://styles/mapbox/outdoors-v12" },
];

if (hasMapboxToken()) {
  mapboxgl.accessToken = MAPBOX_TOKEN;
}

// Theme-aware style: a clean light/dark Mapbox style picked from the html `light`
// class (set by the theme toggle). Both read well; brand overlays sit on top.
function styleForTheme(): string {
  const light =
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("light");
  return light
    ? "mapbox://styles/mapbox/light-v11"
    : "mapbox://styles/mapbox/dark-v11";
}

export type MapboxMap = mapboxgl.Map;

export default function MapView({
  center = CAIRO,
  zoom = 11,
  className,
  interactive = true,
  styleSwitcher = false,
  onReady,
  onMapClick,
  onStyleChange,
}: {
  center?: [number, number];
  zoom?: number;
  className?: string;
  interactive?: boolean;
  /** Show a compact base-map style switcher (Streets/Satellite/…). */
  styleSwitcher?: boolean;
  onReady?: (map: mapboxgl.Map) => void;
  onMapClick?: (lngLat: { lng: number; lat: number }) => void;
  /** Fires after any style reload (theme OR switcher), so callers re-add layers. */
  onStyleChange?: (map: mapboxgl.Map) => void;
}) {
  const { t } = useT();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  // When the user picks a style from the switcher, the theme observer stops
  // auto-swapping so the manual choice sticks across app light/dark toggles.
  const manualStyleRef = useRef<string | null>(null);
  const [styleKey, setStyleKey] = useState("auto");
  const [menuOpen, setMenuOpen] = useState(false);

  // Keep latest callbacks in refs so the once-only init effect never goes stale.
  const onReadyRef = useRef(onReady);
  const onClickRef = useRef(onMapClick);
  const onStyleRef = useRef(onStyleChange);
  onReadyRef.current = onReady;
  onClickRef.current = onMapClick;
  onStyleRef.current = onStyleChange;

  useEffect(() => {
    if (!containerRef.current || mapRef.current || !hasMapboxToken()) return;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: styleForTheme(),
      center,
      zoom,
      interactive,
      attributionControl: true,
    });
    mapRef.current = map;
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => onReadyRef.current?.(map));
    map.on("click", (e) =>
      onClickRef.current?.({ lng: e.lngLat.lng, lat: e.lngLat.lat }),
    );
    map.on("style.load", () => onStyleRef.current?.(map));

    // Swap the basemap when the theme (html.light) toggles. Markers survive a
    // setStyle; layers/sources are re-added by callers via onStyleChange.
    const obs = new MutationObserver(() => {
      if (manualStyleRef.current) return; // respect the user's manual style choice
      const next = styleForTheme();
      const current = map.getStyle()?.sprite ?? "";
      const wantLight = next.includes("light-v11");
      const isLight = current.includes("light");
      if (wantLight !== isLight) map.setStyle(next);
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    return () => {
      obs.disconnect();
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!hasMapboxToken()) {
    return (
      <div
        className={
          (className ?? "h-full w-full") +
          " flex items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/40 p-4 text-center text-sm text-slate-500"
        }
      >
        {t("map.tokenMissing")}
      </div>
    );
  }

  function pickStyle(s: { key: string; url: string | null }) {
    const map = mapRef.current;
    if (!map) return;
    setMenuOpen(false);
    setStyleKey(s.key);
    manualStyleRef.current = s.url; // null for "auto"
    map.setStyle(s.url ?? styleForTheme()); // style.load re-fires onStyleChange
  }

  // dir=ltr + explicit direction keep the map canvas/controls from mirroring
  // under RTL/Arabic (Mapbox otherwise inherits the page's rtl direction).
  return (
    <div dir="ltr" style={{ direction: "ltr" }} className={"relative " + (className ?? "h-full w-full")}>
      <div ref={containerRef} className="h-full w-full" />

      {styleSwitcher && (
        <div className="absolute left-2 top-2 z-10">
          <button
            onClick={() => setMenuOpen((o) => !o)}
            title={t("map.layers")}
            aria-label={t("map.layers")}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-ink-700 bg-ink-900/90 text-slate-200 shadow-lg backdrop-blur hover:text-white"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></svg>
          </button>
          {menuOpen && (
            <ul className="mt-1 w-44 overflow-hidden rounded-lg border border-ink-700 bg-ink-900 shadow-2xl">
              {SWITCHER_STYLES.map((s) => (
                <li key={s.key}>
                  <button
                    onClick={() => pickStyle(s)}
                    className={"block w-full px-3 py-2 text-start text-xs " + (styleKey === s.key ? "bg-brand/15 text-brand-sage" : "text-slate-200 hover:bg-ink-800")}
                  >
                    {t("map.style." + s.key)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// Mapbox helpers. The public token is read ONLY from the env var — never hardcoded.
export const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";
export const hasMapboxToken = (): boolean => MAPBOX_TOKEN.length > 0;

// Default map center: Cairo / Giza. Mapbox uses [lng, lat] order.
export const CAIRO: [number, number] = [31.2357, 30.0444];

// Per-route line color. Brand green is the default when a route has none.
export const DEFAULT_ROUTE_COLOR = "#3AA76D";

// A readable, well-separated palette for the color picker + the all-routes
// overview (used to give uncolored routes distinct fallback colors).
export const ROUTE_PALETTE = [
  "#3AA76D", // brand green
  "#2563EB", // blue
  "#F59E0B", // amber
  "#EF4444", // red
  "#8B5CF6", // violet
  "#EC4899", // pink
  "#14B8A6", // teal
  "#F97316", // orange
  "#0EA5E9", // sky
  "#A3A635", // olive
];

/** A route's draw color: its own color, else a stable palette slot by index. */
export function routeColor(color: string | null | undefined, index = 0): string {
  return color || ROUTE_PALETTE[index % ROUTE_PALETTE.length];
}

export type LngLat = [number, number];
export type LineStringGeometry = { type: "LineString"; coordinates: LngLat[] };

export interface GeocodeResult {
  name: string; // short name
  place: string; // full place label
  center: LngLat; // [lng, lat]
}

/** Forward geocoding biased to Egypt + Cairo. Returns up to 5 candidates. */
export async function geocodePlace(query: string): Promise<GeocodeResult[]> {
  const q = query.trim();
  if (!q || !hasMapboxToken()) return [];
  const url =
    `https://api.mapbox.com/geocoding/v5/mapbox.places/${encodeURIComponent(q)}.json` +
    `?access_token=${MAPBOX_TOKEN}&country=eg&proximity=${CAIRO[0]},${CAIRO[1]}&limit=5`;
  try {
    const res = await fetch(url);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.features ?? []).map(
      (f: { text: string; place_name: string; center: LngLat }) => ({
        name: f.text,
        place: f.place_name,
        center: f.center,
      }),
    );
  } catch {
    return [];
  }
}

/** Reverse geocoding — best-effort short name for a dropped point. */
export async function reverseGeocode(lng: number, lat: number): Promise<string | null> {
  if (!hasMapboxToken()) return null;
  const url =
    `https://api.mapbox.com/geocoding/v5/mapbox.places/${lng},${lat}.json` +
    `?access_token=${MAPBOX_TOKEN}&types=poi,address,neighborhood,place&limit=1`;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    return data.features?.[0]?.text ?? null;
  } catch {
    return null;
  }
}

// --- Geometry helpers (nearest-route, distances) ----------------------------

/** Great-circle distance in metres between two [lng, lat] points. */
export function haversineMeters(a: LngLat, b: LngLat): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const la1 = toRad(a[1]);
  const la2 = toRad(b[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Distance in metres from point p to the segment a-b (local equirectangular
 * projection around p, accurate for the short spans we compare). */
export function pointToSegmentMeters(p: LngLat, a: LngLat, b: LngLat): number {
  const mPerDegLat = 111320;
  const mPerDegLng = 111320 * Math.cos((p[1] * Math.PI) / 180);
  const xy = (c: LngLat): [number, number] => [(c[0] - p[0]) * mPerDegLng, (c[1] - p[1]) * mPerDegLat];
  const A = xy(a);
  const B = xy(b);
  const dx = B[0] - A[0];
  const dy = B[1] - A[1];
  const len2 = dx * dx + dy * dy || 1e-9;
  let t = (-A[0] * dx - A[1] * dy) / len2; // project origin (=p) onto A-B
  t = Math.max(0, Math.min(1, t));
  const cx = A[0] + t * dx;
  const cy = A[1] + t * dy;
  return Math.hypot(cx, cy);
}

/** Nearest route to a point: min point-to-line-segment distance across each
 * route's polyline. Returns the winning route id + its distance in metres. */
export function nearestRoute(
  point: LngLat,
  routes: { id: string; coords: LngLat[] }[],
): { id: string; meters: number } | null {
  let best: { id: string; meters: number } | null = null;
  for (const r of routes) {
    const cs = r.coords;
    if (cs.length === 0) continue;
    let m = cs.length === 1 ? haversineMeters(point, cs[0]) : Infinity;
    for (let i = 0; i < cs.length - 1; i++) m = Math.min(m, pointToSegmentMeters(point, cs[i], cs[i + 1]));
    if (best === null || m < best.meters) best = { id: r.id, meters: m };
  }
  return best;
}

/* eslint-disable @typescript-eslint/no-explicit-any */
/** Add a small white direction-arrow icon to the map (once). */
export function ensureArrowImage(map: any): void {
  if (map.hasImage?.("dir-arrow")) return;
  const size = 24;
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, size, size);
  ctx.beginPath();
  ctx.moveTo(7, 5);
  ctx.lineTo(18, 12);
  ctx.lineTo(7, 19);
  ctx.closePath();
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "rgba(0,0,0,0.65)";
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  const img = ctx.getImageData(0, 0, size, size);
  try {
    map.addImage("dir-arrow", { width: size, height: size, data: new Uint8Array(img.data.buffer) });
  } catch {
    /* already added */
  }
}

/** Draw direction arrows along a line source (start→end), sharing its source. */
export function addArrowLayer(map: any, layerId: string, sourceId: string): void {
  ensureArrowImage(map);
  if (map.getLayer(layerId)) return;
  map.addLayer({
    id: layerId,
    type: "symbol",
    source: sourceId,
    layout: {
      "symbol-placement": "line",
      "symbol-spacing": 110,
      "icon-image": "dir-arrow",
      "icon-size": 0.7,
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    },
  });
}
/* eslint-enable @typescript-eslint/no-explicit-any */

export interface DirectionsResult {
  geometry: LineStringGeometry;
  km: number;
  minutes: number;
}

/** Driving directions through the given waypoints, following streets. */
export async function getDirections(coords: LngLat[]): Promise<DirectionsResult | null> {
  if (coords.length < 2 || !hasMapboxToken()) return null;
  const path = coords.map((c) => `${c[0]},${c[1]}`).join(";");
  const url =
    `https://api.mapbox.com/directions/v5/mapbox/driving/${path}` +
    `?geometries=geojson&overview=full&access_token=${MAPBOX_TOKEN}`;
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    const route = data.routes?.[0];
    if (!route) return null;
    return {
      geometry: route.geometry as LineStringGeometry,
      km: Math.round((route.distance / 1000) * 100) / 100,
      minutes: Math.round(route.duration / 60),
    };
  } catch {
    return null;
  }
}

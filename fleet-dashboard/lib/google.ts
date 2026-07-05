// Google Places — used ONLY for place SEARCH (autocomplete + details).
// Map rendering + the road-following line stay on Mapbox. Key read from env,
// never hardcoded.
//
// Loading: we use Google's official "dynamic library import" bootstrap loader,
// which guarantees google.maps.importLibrary exists and loads each library
// exactly once (safe across React re-renders / Strict Mode double-invoke). We
// then `await importLibrary("places")` ONCE and reuse that fully-ready library
// object — which exposes BOTH the new API (AutocompleteSuggestion / Place) and
// the legacy API (AutocompleteService / PlacesService) — so we never call into
// the API before it's ready.
//
// Failures surface as error CODES (not silently swallowed): "missing-key",
// "auth-failed" (gm_authFailure — invalid key or referrer not allowed),
// "load-failed" (script/network), "request-denied" (API not enabled), "unknown".
/* eslint-disable @typescript-eslint/no-explicit-any */

export const GOOGLE_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "";
export const hasGoogleKey = (): boolean => GOOGLE_KEY.length > 0;

export type GoogleErrorCode =
  | "missing-key"
  | "auth-failed"
  | "load-failed"
  | "request-denied"
  | "unknown";

let authFailed = false;
let placesLibPromise: Promise<any> | null = null;
let sessionToken: any = null;
let acService: any = null; // legacy AutocompleteService
let placesService: any = null; // legacy PlacesService

class GoogleLoadError extends Error {
  code: GoogleErrorCode;
  constructor(code: GoogleErrorCode) {
    super(code);
    this.code = code;
  }
}

/** Mask a key for console diagnostics — never prints the full secret. */
function maskKey(k: string): string {
  if (!k) return "<EMPTY>";
  if (k.length <= 12) return `<short:${k.length} chars>`;
  return `${k.slice(0, 6)}…${k.slice(-6)} (len ${k.length})`;
}

/** Official Google Maps bootstrap loader — defines google.maps.importLibrary. */
function injectBootstrap(key: string): void {
  ((g: any) => {
    let h: any;
    const c = "google";
    const l = "importLibrary";
    const q = "__ib__";
    const m = document;
    let b: any = window;
    b = b[c] || (b[c] = {});
    const d: any = b.maps || (b.maps = {});
    const r = new Set<string>();
    const e = new URLSearchParams();
    const u = () =>
      h ||
      (h = new Promise<void>((f, n) => {
        const a = m.createElement("script");
        e.set("libraries", [...r] + "");
        for (const k in g) e.set(k.replace(/[A-Z]/g, (s) => "_" + s[0].toLowerCase()), g[k]);
        e.set("callback", c + ".maps." + q);
        a.src = `https://maps.${c}apis.com/maps/api/js?` + e;
        d[q] = f;
        a.onerror = () => {
          h = n(new Error("Google Maps could not load."));
        };
        a.nonce = (m.querySelector("script[nonce]") as any)?.nonce || "";
        m.head.append(a);
      }));
    d[l]
      ? console.warn("[google] Maps JS already loaded; ignoring duplicate bootstrap.")
      : (d[l] = (f: string, ...n: any[]) => r.add(f) && u().then(() => d[l](f, ...n)));
  })({ key, v: "weekly" });
}

/** Resolve the fully-ready Places library object (loaded once). */
function ensurePlaces(): Promise<any> {
  if (typeof window === "undefined") return Promise.reject(new GoogleLoadError("load-failed"));
  if (!hasGoogleKey()) return Promise.reject(new GoogleLoadError("missing-key"));
  if (placesLibPromise) return placesLibPromise;

  // Definitive diagnostic: what the BROWSER is actually running with. Compare
  // these two lines against Google Cloud — they end the guessing.
  console.info(
    `[google] loading Maps JS\n  origin = ${window.location.origin}\n  key    = ${maskKey(GOOGLE_KEY)}`,
  );

  // Google calls this global when the KEY itself is rejected (invalid key, or
  // this URL is not in the key's allowed HTTP referrers). The library still
  // "loads" in that case, so this is the only reliable signal.
  (window as any).gm_authFailure = () => {
    authFailed = true;
    console.error(
      "[google] gm_authFailure — this key was REJECTED for this origin.\n" +
        `  origin = ${window.location.origin}   (must be whitelisted as ${window.location.origin}/*)\n` +
        `  key    = ${maskKey(GOOGLE_KEY)}\n` +
        "  Fix in Google Cloud → Credentials → this key:\n" +
        `    • Application restrictions → Websites → add ${window.location.origin}/*\n` +
        "    • API restrictions → allow 'Maps JavaScript API' + 'Places API'\n" +
        "    • Ensure both APIs are ENABLED in the project (Library). Propagation can take ~5 min.",
    );
  };

  if (!(window as any).google?.maps?.importLibrary) injectBootstrap(GOOGLE_KEY);

  const p: Promise<any> = (window as any).google.maps
    .importLibrary("places")
    .catch((err: unknown) => {
      placesLibPromise = null; // allow a retry on the next keystroke
      console.error("[google] importLibrary('places') failed:", err);
      throw new GoogleLoadError("load-failed");
    });
  placesLibPromise = p;
  return p;
}

export interface Suggestion {
  description: string;
  placeId: string;
}

export interface AutocompleteResult {
  suggestions: Suggestion[];
  error: GoogleErrorCode | null;
}

/** Autocomplete predictions, biased to Egypt. Reports an error code on failure. */
export async function googleAutocomplete(input: string): Promise<AutocompleteResult> {
  if (!input.trim()) return { suggestions: [], error: null };
  if (!hasGoogleKey()) return { suggestions: [], error: "missing-key" };

  let places: any;
  try {
    places = await ensurePlaces();
  } catch (e) {
    return { suggestions: [], error: e instanceof GoogleLoadError ? e.code : "load-failed" };
  }
  if (authFailed) return { suggestions: [], error: "auth-failed" };

  // --- Preferred: new Places API (AutocompleteSuggestion) ---
  try {
    if (places?.AutocompleteSuggestion?.fetchAutocompleteSuggestions) {
      if (!sessionToken) sessionToken = new places.AutocompleteSessionToken();
      const { suggestions } = await places.AutocompleteSuggestion.fetchAutocompleteSuggestions({
        input,
        sessionToken,
        includedRegionCodes: ["eg"],
      });
      if (authFailed) return { suggestions: [], error: "auth-failed" };
      return {
        suggestions: (suggestions ?? [])
          .map((s: any) => s.placePrediction)
          .filter(Boolean)
          .map((p: any) => ({ description: p.text?.text ?? "", placeId: p.placeId })),
        error: null,
      };
    }
  } catch (err) {
    console.warn("[google] new Places API failed, trying legacy AutocompleteService:", err);
  }

  // --- Fallback: legacy AutocompleteService (from the same places library) ---
  try {
    if (!places?.AutocompleteService) return { suggestions: [], error: "unknown" };
    if (!acService) acService = new places.AutocompleteService();
    const { preds, status } = await new Promise<{ preds: any[] | null; status: string }>(
      (resolve) =>
        acService.getPlacePredictions(
          { input, componentRestrictions: { country: "eg" } },
          (p: any[] | null, st: string) => resolve({ preds: p, status: st }),
        ),
    );
    if (authFailed) return { suggestions: [], error: "auth-failed" };
    if (status === "OK") {
      return {
        suggestions: (preds ?? []).map((p) => ({ description: p.description, placeId: p.place_id })),
        error: null,
      };
    }
    if (status === "ZERO_RESULTS") return { suggestions: [], error: null };
    if (status === "REQUEST_DENIED") {
      console.error(
        "[google] AutocompleteService REQUEST_DENIED — enable the Places API for this key " +
          "(APIs & Services → Library → 'Places API' and/or 'Places API (New)').",
      );
      return { suggestions: [], error: "request-denied" };
    }
    console.error("[google] AutocompleteService status:", status);
    return { suggestions: [], error: "unknown" };
  } catch (err) {
    console.error("[google] autocomplete failed:", err);
    return { suggestions: [], error: "unknown" };
  }
}

export interface PlaceLocation {
  name: string;
  lng: number;
  lat: number;
}

/** Resolve a prediction to a name + coordinates. */
export async function googlePlaceDetails(placeId: string): Promise<PlaceLocation | null> {
  if (!hasGoogleKey()) return null;
  let places: any;
  try {
    places = await ensurePlaces();
  } catch {
    return null;
  }
  if (authFailed) return null;

  // --- Preferred: new Place class ---
  try {
    if (places?.Place) {
      const place = new places.Place({ id: placeId });
      await place.fetchFields({ fields: ["displayName", "location"] });
      sessionToken = null; // end the autocomplete session (billing)
      if (place.location) {
        return {
          name: place.displayName ?? "",
          lng: place.location.lng(),
          lat: place.location.lat(),
        };
      }
    }
  } catch (err) {
    console.warn("[google] new Place details failed, trying legacy:", err);
  }

  // --- Fallback: legacy PlacesService ---
  try {
    if (!places?.PlacesService) return null;
    if (!placesService) placesService = new places.PlacesService(document.createElement("div"));
    return await new Promise<PlaceLocation | null>((resolve) =>
      placesService.getDetails(
        { placeId, fields: ["name", "geometry"] },
        (place: any, status: string) => {
          sessionToken = null;
          const loc = place?.geometry?.location;
          if (status !== "OK" || !loc) {
            resolve(null);
            return;
          }
          resolve({ name: place.name ?? "", lng: loc.lng(), lat: loc.lat() });
        },
      ),
    );
  } catch {
    return null;
  }
}

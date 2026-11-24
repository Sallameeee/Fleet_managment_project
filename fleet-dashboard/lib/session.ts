// Session tokens + silent renewal for the dashboard: org users ("manager") and
// the super-admin panel ("admin") share this one implementation.
//
// The dashboard never logs anyone out by itself. The rules:
//  * Renewal is single-flight inside a tab AND across tabs (Web Locks): one tab
//    renews, the others find the new pair in localStorage and reuse it. Supabase
//    rotates refresh tokens, so two tabs must never spend the same one.
//  * The NEW refresh token is stored before the new access token is used.
//  * 401 "refresh_token_already_used": another tab rotated it first. Re-read
//    storage and carry on. Never a logout.
//  * Offline, timeout, 5xx, 503, 429: keep the session, retry with backoff.
//  * The server no longer knows the session: keep everything on screen and ask
//    for the password again (a re-auth modal; see onSessionState).
//  * 403 "deactivated": the ONE case that signs out.

export type SessionKind = "manager" | "admin";
export type RefreshOutcome = "refreshed" | "adopted" | "transient" | "dead" | "forbidden" | "none";
export type SessionState = "ok" | "reauth" | "forbidden";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const SESSION_KEYS: Record<SessionKind, { token: string; refresh: string }> = {
  manager: { token: "fleet_manager_token", refresh: "fleet_manager_refresh" },
  admin: { token: "fleet_admin_token", refresh: "fleet_admin_refresh" },
};

/** Marker of an impersonated org view (super admin acting as an org owner). */
export const IMPERSONATION_KEY = "fleet_manager_impersonation";

const REFRESH_TIMEOUT_MS = 15_000;
const DEAD_RETRY_MS = 5 * 60_000;
const MAX_BACKOFF_MS = 120_000;

// --- storage (read on EVERY call: another tab may have renewed) --------------
const memory: Record<string, string | null> = {};

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readKey(key: string): string | null {
  const s = storage();
  if (s) {
    try {
      return s.getItem(key);
    } catch {
      /* private mode quirks: fall back to memory */
    }
  }
  return memory[key] ?? null;
}

export function writeKey(key: string, value: string | null): void {
  memory[key] = value;
  const s = storage();
  if (!s) return;
  try {
    if (value === null) s.removeItem(key);
    else s.setItem(key, value);
  } catch {
    /* quota / private mode: the memory copy still works for this tab */
  }
}

export function getSessionToken(kind: SessionKind): string | null {
  return readKey(SESSION_KEYS[kind].token);
}

export function getRefreshToken(kind: SessionKind): string | null {
  return readKey(SESSION_KEYS[kind].refresh);
}

/** Store a fresh pair (login, re-login, renewal): the refresh token FIRST. */
export function storeSession(kind: SessionKind, access: string, refresh?: string | null): void {
  if (refresh) writeKey(SESSION_KEYS[kind].refresh, refresh);
  writeKey(SESSION_KEYS[kind].token, access);
  resetBackoff(kind);
  emit(kind, "ok");
}

export function clearSession(kind: SessionKind): void {
  writeKey(SESSION_KEYS[kind].token, null);
  writeKey(SESSION_KEYS[kind].refresh, null);
  resetBackoff(kind);
}

// --- state events (the layouts show the re-auth modal / sign out) -------------
type Listener = (kind: SessionKind, state: SessionState) => void;
const listeners = new Set<Listener>();

export function onSessionState(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(kind: SessionKind, state: SessionState): void {
  for (const l of Array.from(listeners)) {
    try {
      l(kind, state);
    } catch {
      /* a listener's bug must not break renewal */
    }
  }
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  // Another tab signed in again (or renewed): this tab's modal can close.
  window.addEventListener("storage", (e: StorageEvent) => {
    for (const kind of ["manager", "admin"] as SessionKind[]) {
      if (e.key === SESSION_KEYS[kind].token && e.newValue) {
        resetBackoff(kind);
        emit(kind, "ok");
      }
    }
  });
}

// --- backoff ------------------------------------------------------------------
const notBefore: Record<SessionKind, number> = { manager: 0, admin: 0 };
const backoff: Record<SessionKind, number> = { manager: 0, admin: 0 };
const lastFailure: Record<SessionKind, RefreshOutcome | null> = { manager: null, admin: null };

function resetBackoff(kind: SessionKind): void {
  notBefore[kind] = 0;
  backoff[kind] = 0;
  lastFailure[kind] = null;
}

function fail(kind: SessionKind, outcome: RefreshOutcome): RefreshOutcome {
  lastFailure[kind] = outcome;
  if (outcome === "dead") {
    notBefore[kind] = Date.now() + DEAD_RETRY_MS;
  } else {
    backoff[kind] = backoff[kind] ? Math.min(backoff[kind] * 2, MAX_BACKOFF_MS) : 5_000;
    notBefore[kind] = Date.now() + backoff[kind];
  }
  return outcome;
}

// --- cross-tab lock -------------------------------------------------------------
async function withLock<T>(kind: SessionKind, fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? (navigator as { locks?: LockManager }).locks : undefined;
  if (locks && typeof locks.request === "function") {
    return locks.request(`routemind-auth-refresh-${kind}`, { mode: "exclusive" }, () => fn()) as Promise<T>;
  }
  // No Web Locks (old browser / insecure context): still single-flight in this
  // tab, and the re-read + Supabase's leniency for the parent token cover tabs.
  return fn();
}

async function postJson(path: string, body: unknown): Promise<Response> {
  const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), REFRESH_TIMEOUT_MS) : null;
  try {
    return await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctl?.signal,
    });
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// --- renewal ----------------------------------------------------------------------
const inflight: Partial<Record<SessionKind, Promise<RefreshOutcome>>> = {};

/** Renew after `failedToken` got a 401. Shared by every caller in this tab. */
export function refreshSession(kind: SessionKind, failedToken?: string | null): Promise<RefreshOutcome> {
  const running = inflight[kind];
  if (running) return running;
  const p = withLock(kind, () => doRefresh(kind, failedToken ?? null)).finally(() => {
    inflight[kind] = undefined;
  });
  inflight[kind] = p;
  return p;
}

async function doRefresh(kind: SessionKind, failed: string | null): Promise<RefreshOutcome> {
  const cur = getSessionToken(kind);
  if (!cur) return "none";
  // Renewed by another tab (or a re-login) while we waited: nothing to send.
  if (failed && cur !== failed) {
    resetBackoff(kind);
    return "adopted";
  }
  if (Date.now() < notBefore[kind]) return lastFailure[kind] ?? "transient";
  if (kind === "manager" && !getRefreshToken(kind) && readKey(IMPERSONATION_KEY)) return renewImpersonation();

  const tried = new Set<string>();
  for (let i = 0; i < 2; i++) {
    const rt = getRefreshToken(kind);
    if (!rt || tried.has(rt)) break;
    tried.add(rt);
    let res: Response;
    try {
      res = await postJson("/auth/refresh", { refresh_token: rt });
    } catch {
      return fail(kind, "transient"); // offline / timeout / DNS
    }
    if (res.ok) {
      let data: { access_token?: string; refresh_token?: string } | null = null;
      try {
        data = await res.json();
      } catch {
        /* handled below */
      }
      if (!data?.access_token) return fail(kind, "transient");
      storeSession(kind, data.access_token, data.refresh_token || rt);
      return "refreshed";
    }
    if (res.status === 403) return "forbidden";
    if (res.status === 400 || res.status === 401 || res.status === 404 || res.status === 422) {
      // Unusable token. "already used" = another tab rotated it: its result is in
      // storage (adopt) or is a different refresh token (next loop).
      const now = getSessionToken(kind);
      if (now && now !== cur) {
        resetBackoff(kind);
        return "adopted";
      }
      continue;
    }
    return fail(kind, "transient"); // 5xx / 503 / 429
  }
  return fail(kind, "dead");
}

/** An impersonated org view runs on a 30-minute token with no refresh token:
 *  re-issue it through the super admin's own (renewable) session. */
async function renewImpersonation(): Promise<RefreshOutcome> {
  let orgId: string | undefined;
  try {
    orgId = (JSON.parse(readKey(IMPERSONATION_KEY) || "null") as { org_id?: string } | null)?.org_id;
  } catch {
    /* corrupt marker */
  }
  if (!orgId || !getSessionToken("admin")) return fail("manager", "dead");
  let res: Response;
  try {
    res = await authFetch("admin", `/organizations/${orgId}/impersonate`, { method: "POST" });
  } catch {
    return fail("manager", "transient");
  }
  if (res.ok) {
    const d = (await res.json().catch(() => null)) as { access_token?: string } | null;
    if (d?.access_token) {
      storeSession("manager", d.access_token, null);
      return "refreshed";
    }
    return fail("manager", "transient");
  }
  if (res.status === 401 || res.status === 403 || res.status === 404) return fail("manager", "dead");
  return fail("manager", "transient");
}

/** fetch() with the session's Bearer token. A 401 renews once and retries; a
 *  session the server no longer knows raises the "reauth" state; nothing here
 *  ever clears the session. */
export async function authFetch(kind: SessionKind, path: string, options: RequestInit = {}): Promise<Response> {
  const send = (tok: string | null) => {
    const headers = new Headers(options.headers);
    if (tok) headers.set("Authorization", `Bearer ${tok}`);
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    return fetch(`${API_URL}${path}`, { ...options, headers });
  };
  const used = getSessionToken(kind);
  let res = await send(used);
  if (res.status === 401 && used) {
    const out = await refreshSession(kind, used);
    if (out === "refreshed" || out === "adopted") {
      res = await send(getSessionToken(kind));
    } else if (out === "dead") {
      emit(kind, "reauth");
    } else if (out === "forbidden") {
      emit(kind, "forbidden");
    }
  } else if (res.status === 403 && used) {
    // Only the deactivated / suspended wording signs out; other 403s are plain
    // permission answers for the page to show.
    const detail = await res
      .clone()
      .json()
      .then((b: { detail?: unknown }) => (typeof b?.detail === "string" ? b.detail.toLowerCase() : ""))
      .catch(() => "");
    if (detail.includes("deactivated") || detail.includes("suspended")) emit(kind, "forbidden");
  }
  return res;
}

/** Why loading the signed-in profile failed. Only "forbidden" may sign out. */
export class SessionError extends Error {
  constructor(
    public reason: "auth" | "forbidden" | "network" | "server",
    message?: string,
  ) {
    super(message ?? reason);
    this.name = "SessionError";
  }
}

/** GET a "who am I" endpoint and classify any failure. */
export async function fetchMe<T>(kind: SessionKind, path: string): Promise<T> {
  let res: Response;
  try {
    res = await authFetch(kind, path);
  } catch {
    throw new SessionError("network");
  }
  if (res.status === 401) throw new SessionError("auth");
  if (res.status === 403) {
    let detail = "Forbidden";
    try {
      const b = await res.json();
      if (typeof b?.detail === "string") detail = b.detail;
    } catch {
      /* non-JSON */
    }
    throw new SessionError("forbidden", detail);
  }
  if (!res.ok) throw new SessionError("server", `HTTP ${res.status}`);
  return (await res.json()) as T;
}

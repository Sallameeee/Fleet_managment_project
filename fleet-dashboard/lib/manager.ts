// Manager (org-user) API client.
//
// Kept SEPARATE from the super-admin client (lib/api.ts): its own token key, so
// the two login flows never clobber each other. Managers authenticate via the
// existing POST /auth/login with `username@org-slug`.

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "fleet_manager_token";
const SLUG_KEY = "fleet_manager_slug";
const IMP_KEY = "fleet_manager_impersonation";

let inMemoryToken: string | null = null;

export function managerGetToken(): string | null {
  if (inMemoryToken) return inMemoryToken;
  if (typeof window !== "undefined") {
    inMemoryToken = window.localStorage.getItem(TOKEN_KEY);
  }
  return inMemoryToken;
}

export function managerSetToken(token: string): void {
  inMemoryToken = token;
  if (typeof window !== "undefined") window.localStorage.setItem(TOKEN_KEY, token);
}

export function managerClearToken(): void {
  inMemoryToken = null;
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(SLUG_KEY);
    window.localStorage.removeItem(IMP_KEY);
  }
}

export interface Impersonation {
  org_id: string;
  org_name: string;
  org_slug: string;
  owner_name: string;
  by: string;
}

/** Start an impersonated manager session from a super-admin-issued token. */
export function startImpersonation(token: string, info: Impersonation): void {
  managerSetToken(token);
  if (typeof window !== "undefined") {
    window.localStorage.setItem(SLUG_KEY, info.org_slug);
    window.localStorage.setItem(IMP_KEY, JSON.stringify(info));
  }
}

export function getImpersonation(): Impersonation | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(IMP_KEY);
  try {
    return raw ? (JSON.parse(raw) as Impersonation) : null;
  } catch {
    return null;
  }
}

export function managerLogout(): void {
  managerClearToken();
}

export function getManagerSlug(): string | null {
  if (typeof window !== "undefined") return window.localStorage.getItem(SLUG_KEY);
  return null;
}

export type Permissions = Record<string, boolean>;

export interface ManagerProfile {
  id: string;
  name: string;
  username?: string;
  role: string;
  org_id: string;
  permissions: Permissions | null;
}

interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  user: {
    id: string;
    name: string;
    role: string;
    org_id: string;
    permissions: Permissions;
  };
}

async function extractError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

/** Authenticate an org user. Combines username + slug into `username@org-slug`. */
export async function managerLogin(username: string, slug: string, password: string) {
  const cleanSlug = slug.trim().toLowerCase();
  const login = `${username.trim()}@${cleanSlug}`;
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login, password }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Login failed. Check your credentials."));
  const data = (await res.json()) as LoginResponse;
  managerSetToken(data.access_token);
  if (typeof window !== "undefined") window.localStorage.setItem(SLUG_KEY, cleanSlug);
  return data.user;
}

/** fetch() wrapper that attaches the MANAGER Bearer token. */
export async function managerFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = managerGetToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return fetch(`${API_URL}${path}`, { ...options, headers });
}

/** Server-verified guard: GET /auth/me. Throws on 401 so callers can redirect. */
export async function managerMe(): Promise<ManagerProfile> {
  const res = await managerFetch("/auth/me");
  if (!res.ok) throw new Error("Not authorized");
  return (await res.json()) as ManagerProfile;
}

/**
 * Permission gate for the UI. Mirrors the backend's require_permission: an owner
 * always passes; otherwise the permissions map must have the flag set true.
 */
export function canAccess(
  profile: Pick<ManagerProfile, "role" | "permissions">,
  perm: string,
): boolean {
  return profile.role === "owner" || profile.permissions?.[perm] === true;
}

/** Public per-vehicle passenger tracking URL (backend /track endpoint). */
export function trackingUrl(shareToken: string): string {
  return `${API_URL}/track/${shareToken}`;
}

// --- Drivers -----------------------------------------------------------------

export interface ManagerDriver {
  id: string;
  name: string;
  username: string;
  phone?: string | null;
  is_active: boolean;
  created_at?: string;
  online: boolean;
  current_vehicle: string | null;
}

export async function listDrivers(): Promise<ManagerDriver[]> {
  const res = await managerFetch("/drivers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load drivers."));
  return (await res.json()).drivers as ManagerDriver[];
}

export interface CreateDriverInput {
  name: string;
  username: string;
  password: string;
  phone?: string;
  email?: string;
}

export interface DriverCreateResult {
  id: string;
  name: string;
  username: string;
  login_email: string;
  role: string;
}

export async function createDriver(input: CreateDriverInput): Promise<DriverCreateResult> {
  const res = await managerFetch("/drivers", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create driver."));
  return (await res.json()) as DriverCreateResult;
}

// --- Vehicles ----------------------------------------------------------------

export interface ManagerVehicle {
  id: string;
  bus_number: string;
  plate_number: string | null;
  share_token: string;
  is_active: boolean;
  created_at?: string;
}

export async function listVehicles(): Promise<ManagerVehicle[]> {
  const res = await managerFetch("/vehicles");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load vehicles."));
  return (await res.json()).vehicles as ManagerVehicle[];
}

export interface CreateVehicleInput {
  bus_number: string;
  plate_number?: string;
}

export async function createVehicle(input: CreateVehicleInput): Promise<ManagerVehicle> {
  const res = await managerFetch("/vehicles", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create vehicle."));
  return (await res.json()) as ManagerVehicle;
}

// --- Dashboard summary -------------------------------------------------------

export interface DriverCounts {
  total: number;
  online: number;
  offline: number;
  working_now: number;
}

export interface TopDriver {
  driver_id: string;
  name: string | null;
  actual_km: number;
  trips: number;
  score: number | null;
}

export interface AlertFeedItem {
  id: string;
  type: string;
  detail: string | null;
  occurred_at: string | null;
  is_read: boolean;
  driver_name: string | null;
}

export interface DashboardSummary {
  drivers: DriverCounts;
  top_driver: TopDriver | null;
  alerts: AlertFeedItem[];
}

export async function getDashboardSummary(): Promise<DashboardSummary> {
  const res = await managerFetch("/dashboard/summary");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load dashboard."));
  return (await res.json()) as DashboardSummary;
}

// --- Routes ------------------------------------------------------------------

export interface RouteStop {
  id?: string;
  name: string;
  lat: number;
  lng: number;
  stop_order: number;
  dwell_minutes: number;
}

export interface ManagerRoute {
  id: string;
  name: string;
  total_km: number | null;
  est_minutes: number | null;
  is_active: boolean;
  created_at?: string;
  stops: RouteStop[];
}

export async function listRoutes(): Promise<ManagerRoute[]> {
  const res = await managerFetch("/routes");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load routes."));
  return (await res.json()).routes as ManagerRoute[];
}

export interface CreateRouteInput {
  name: string;
  total_km?: number;
  est_minutes?: number;
  stops: RouteStop[];
}

export async function createRoute(input: CreateRouteInput): Promise<ManagerRoute> {
  const res = await managerFetch("/routes", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create route."));
  return (await res.json()) as ManagerRoute;
}

// --- Assignments -------------------------------------------------------------

export interface ManagerAssignment {
  id: string;
  trip_date: string;
  shift_label: string | null;
  start_time: string | null;
  driver_id: string;
  driver_name: string | null;
  route_id: string;
  route_name: string | null;
  vehicle_id: string;
  vehicle_bus_number: string | null;
  created_at?: string;
}

export async function listAssignments(date?: string): Promise<ManagerAssignment[]> {
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  const res = await managerFetch(`/assignments${q}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to load assignments."));
  return (await res.json()).assignments as ManagerAssignment[];
}

export interface CreateAssignmentInput {
  driver_id: string;
  route_id: string;
  vehicle_id: string;
  trip_date: string;
  shift_label?: string;
  start_time?: string;
}

export async function createAssignment(input: CreateAssignmentInput): Promise<ManagerAssignment> {
  const res = await managerFetch("/assignments", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create assignment."));
  return (await res.json()) as ManagerAssignment;
}

// --- Alerts ------------------------------------------------------------------

export interface ManagerAlert {
  id: string;
  type: string;
  detail: string | null;
  lat: number | null;
  lng: number | null;
  occurred_at: string | null;
  is_read: boolean;
  driver_name: string | null;
  route_name: string | null;
  vehicle_bus_number: string | null;
}

export interface AlertFilters {
  type?: string;
  is_read?: boolean;
  date?: string;
}

export async function listAlerts(filters: AlertFilters = {}): Promise<ManagerAlert[]> {
  const p = new URLSearchParams();
  if (filters.type) p.set("type", filters.type);
  if (filters.is_read !== undefined) p.set("is_read", String(filters.is_read));
  if (filters.date) p.set("date", filters.date);
  const qs = p.toString();
  const res = await managerFetch(`/alerts${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to load alerts."));
  return (await res.json()).alerts as ManagerAlert[];
}

export async function getUnreadAlertCount(): Promise<number> {
  const res = await managerFetch("/alerts?is_read=false");
  if (!res.ok) return 0;
  const data = await res.json();
  return typeof data.count === "number" ? data.count : (data.alerts?.length ?? 0);
}

export async function markAlertRead(id: string, isRead = true): Promise<void> {
  const res = await managerFetch(`/alerts/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ is_read: isRead }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update alert."));
}

// --- Alert rules -------------------------------------------------------------

export interface AlertRule {
  id: string;
  name: string;
  type: string;
  threshold: number | null;
  target_kind: "all" | "vehicles" | "drivers";
  target_ids: string[] | null;
  notify_panel: boolean;
  notify_email: boolean;
  notify_push: boolean;
  is_active: boolean;
  created_at?: string;
}

export async function listAlertRules(): Promise<AlertRule[]> {
  const res = await managerFetch("/alert-rules");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load alert rules."));
  return (await res.json()).alert_rules as AlertRule[];
}

export interface CreateAlertRuleInput {
  name: string;
  type: string;
  threshold?: number | null;
  target_kind: "all" | "vehicles" | "drivers";
  target_ids?: string[] | null;
  notify_panel: boolean;
  notify_email: boolean;
  notify_push: boolean;
  is_active: boolean;
}

export async function createAlertRule(input: CreateAlertRuleInput): Promise<AlertRule> {
  const res = await managerFetch("/alert-rules", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create alert rule."));
  return (await res.json()) as AlertRule;
}

export async function updateAlertRule(
  id: string,
  patch: Partial<CreateAlertRuleInput>,
): Promise<AlertRule> {
  const res = await managerFetch(`/alert-rules/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update alert rule."));
  return (await res.json()) as AlertRule;
}

export async function deleteAlertRule(id: string): Promise<void> {
  const res = await managerFetch(`/alert-rules/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete alert rule."));
}

// --- Reports -----------------------------------------------------------------

export interface ReportParams {
  types: string[];
  period?: string;
  date_from?: string;
  date_to?: string;
}

export interface ReportResponse {
  org: { id: string; name: string };
  period: { from: string; to: string; preset: string };
  sections: {
    drivers?: Array<{
      driver_id: string;
      driver_name: string | null;
      trips: number;
      actual_km: number;
      planned_km: number;
      difference_km: number;
      alerts: Record<string, number>;
    }>;
    trips?: Array<{
      trip_id: string;
      driver_name: string | null;
      route_name: string | null;
      vehicle_bus_number: string | null;
      started_at: string | null;
      ended_at: string | null;
      status: string;
      planned_km: number | null;
      actual_km: number;
      difference_km: number;
    }>;
    kilometers?: {
      by_vehicle: Array<{ vehicle_bus_number: string | null; planned_km: number; actual_km: number; difference_km: number }>;
      by_driver: Array<{ driver_name: string | null; planned_km: number; actual_km: number; difference_km: number }>;
    };
    speed?: Array<{
      driver_name: string | null;
      max_speed: number | null;
      avg_speed: number | null;
      speeding_alerts: number;
    }>;
  };
}

function reportQuery(params: ReportParams): string {
  const p = new URLSearchParams();
  p.set("types", params.types.join(","));
  if (params.period) p.set("period", params.period);
  if (params.date_from) p.set("date_from", params.date_from);
  if (params.date_to) p.set("date_to", params.date_to);
  return p.toString();
}

export async function getReport(params: ReportParams): Promise<ReportResponse> {
  const res = await managerFetch(`/reports?${reportQuery(params)}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to generate report."));
  return (await res.json()) as ReportResponse;
}

/** Fetch the PDF (with auth) and trigger a browser download. */
export async function downloadReportPdf(params: ReportParams): Promise<void> {
  const res = await managerFetch(`/reports/pdf?${reportQuery(params)}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to download PDF."));
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `report_${params.period ?? "custom"}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// --- Live tracking (Full View) ----------------------------------------------

export interface LiveDriver {
  driver_id: string;
  name: string | null;
  vehicle_bus_number: string | null;
  route_name: string | null;
  position: { lat: number; lng: number; recorded_at: string } | null;
  online: boolean;
}

export async function getLiveDrivers(): Promise<LiveDriver[]> {
  const res = await managerFetch("/live/drivers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load live drivers."));
  return (await res.json()).drivers as LiveDriver[];
}

// --- Settings: tracking hours ------------------------------------------------

export interface TrackingHours {
  tracking_start_time: string | null;
  tracking_end_time: string | null;
  mode: "always_on" | "windowed";
}

export async function getTrackingHours(): Promise<TrackingHours> {
  const res = await managerFetch("/organizations/tracking-hours");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load settings."));
  return (await res.json()) as TrackingHours;
}

export async function setTrackingHours(
  start: string | null,
  end: string | null,
): Promise<TrackingHours> {
  const res = await managerFetch("/organizations/tracking-hours", {
    method: "PATCH",
    body: JSON.stringify({ tracking_start_time: start, tracking_end_time: end }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to save settings."));
  return (await res.json()) as TrackingHours;
}

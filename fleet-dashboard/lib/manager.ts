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
  module?: string; // 'university' (default) | 'school' — drives feature relabels
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
  license_number?: string | null;
  license_start_date?: string | null;
  license_expiry_date?: string | null;
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
  license_number?: string;
  license_start_date?: string;
  license_expiry_date?: string;
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

export interface UpdateDriverInput {
  name?: string;
  phone?: string | null;
  is_active?: boolean;
  license_number?: string | null;
  license_start_date?: string | null;
  license_expiry_date?: string | null;
}

export async function updateDriver(id: string, input: UpdateDriverInput): Promise<void> {
  const res = await managerFetch(`/drivers/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update driver."));
}

export async function deleteDriver(id: string): Promise<void> {
  const res = await managerFetch(`/drivers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete driver."));
}

// --- Vehicles ----------------------------------------------------------------

export interface ManagerVehicle {
  id: string;
  bus_number: string;
  plate_number: string | null;
  capacity?: number | null;
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
  capacity?: number | null;
}

export async function createVehicle(input: CreateVehicleInput): Promise<ManagerVehicle> {
  const res = await managerFetch("/vehicles", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create vehicle."));
  return (await res.json()) as ManagerVehicle;
}

export interface UpdateVehicleInput {
  bus_number?: string;
  plate_number?: string | null;
  capacity?: number | null;
  is_active?: boolean;
}

export async function updateVehicle(id: string, input: UpdateVehicleInput): Promise<ManagerVehicle> {
  const res = await managerFetch(`/vehicles/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update vehicle."));
  return (await res.json()) as ManagerVehicle;
}

export async function deleteVehicle(id: string): Promise<void> {
  const res = await managerFetch(`/vehicles/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete vehicle."));
}

// --- Bus drivers (School module — data-only entities, no login) --------------

export interface BusDriver {
  id: string;
  name: string;
  phone: string | null;
  license_number: string | null;
  license_start_date: string | null;
  license_end_date: string | null;
  created_at?: string;
}

export interface BusDriverInput {
  name: string;
  phone?: string | null;
  license_number?: string | null;
  license_start_date?: string | null;
  license_end_date?: string | null;
}

export async function listBusDrivers(): Promise<BusDriver[]> {
  const res = await managerFetch("/bus-drivers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load bus drivers."));
  return (await res.json()).bus_drivers as BusDriver[];
}

export async function createBusDriver(input: BusDriverInput): Promise<BusDriver> {
  const res = await managerFetch("/bus-drivers", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create bus driver."));
  return (await res.json()) as BusDriver;
}

export async function updateBusDriver(id: string, input: BusDriverInput): Promise<BusDriver> {
  const res = await managerFetch(`/bus-drivers/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update bus driver."));
  return (await res.json()) as BusDriver;
}

export async function deleteBusDriver(id: string): Promise<void> {
  const res = await managerFetch(`/bus-drivers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete bus driver."));
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
  arrival_time?: string | null; // "HH:MM"
}

export interface ManagerRoute {
  id: string;
  name: string;
  total_km: number | null;
  est_minutes: number | null;
  start_time?: string | null;
  color?: string | null;
  geometry?: { type: "LineString"; coordinates: [number, number][] } | null;
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
  start_time?: string; // "HH:MM" — departure of the first stop
  color?: string; // hex line color
  stops: RouteStop[];
  // GeoJSON LineString of the road-following path (from Mapbox Directions).
  geometry?: { type: "LineString"; coordinates: [number, number][] };
}

export async function createRoute(input: CreateRouteInput): Promise<ManagerRoute> {
  const res = await managerFetch("/routes", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create route."));
  return (await res.json()) as ManagerRoute;
}

export async function updateRoute(id: string, input: CreateRouteInput): Promise<ManagerRoute> {
  const res = await managerFetch(`/routes/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update route."));
  return (await res.json()) as ManagerRoute;
}

export async function deleteRoute(id: string): Promise<void> {
  const res = await managerFetch(`/routes/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete route."));
}

// --- Assignments -------------------------------------------------------------

export interface ManagerAssignment {
  id: string;
  trip_date: string;
  shift_label: string | null;
  start_time: string | null;
  end_time: string | null;
  driver_id: string;
  driver_name: string | null;
  route_id: string;
  route_name: string | null;
  vehicle_id: string;
  vehicle_bus_number: string | null;
  bus_driver_id?: string | null; // school module: linked bus_drivers row
  bus_driver_name?: string | null; // enriched from bus_drivers
  bus_driver_phone?: string | null;
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
  end_time?: string;
  bus_driver_id?: string | null;
}

// Structured 409 payload from the backend when a driver/vehicle is double-booked.
export interface AssignmentConflict {
  code: "conflict";
  resource: "driver" | "vehicle";
  name: string | null;
  route_name: string | null;
  start: string;
  end: string;
}

export class AssignmentConflictError extends Error {
  conflict: AssignmentConflict;
  constructor(conflict: AssignmentConflict) {
    super("assignment conflict");
    this.name = "AssignmentConflictError";
    this.conflict = conflict;
  }
}

// Throw a structured conflict on 409, otherwise a plain Error.
async function throwAssignmentError(res: Response, fallback: string): Promise<never> {
  if (res.status === 409) {
    try {
      const data = await res.clone().json();
      const d = data?.detail;
      if (d && typeof d === "object" && d.code === "conflict") {
        throw new AssignmentConflictError(d as AssignmentConflict);
      }
    } catch (e) {
      if (e instanceof AssignmentConflictError) throw e;
    }
  }
  throw new Error(await extractError(res, fallback));
}

export async function createAssignment(input: CreateAssignmentInput): Promise<ManagerAssignment> {
  const res = await managerFetch("/assignments", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) await throwAssignmentError(res, "Failed to create assignment.");
  return (await res.json()) as ManagerAssignment;
}

export async function updateAssignment(
  id: string,
  input: CreateAssignmentInput,
): Promise<ManagerAssignment> {
  const res = await managerFetch(`/assignments/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) await throwAssignmentError(res, "Failed to update assignment.");
  return (await res.json()) as ManagerAssignment;
}

export async function deleteAssignment(id: string): Promise<void> {
  const res = await managerFetch(`/assignments/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete assignment."));
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

/** Mark every unread alert in the org as read; returns how many were updated. */
export async function markAllAlertsRead(): Promise<number> {
  const res = await managerFetch("/alerts/mark-all-read", { method: "POST" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to mark all as read."));
  return (await res.json()).updated ?? 0;
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
  vehicle_id?: string;
  driver_id?: string;
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
  if (params.vehicle_id) p.set("vehicle_id", params.vehicle_id);
  if (params.driver_id) p.set("driver_id", params.driver_id);
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

// --- Report schedules (email delivery deferred to Firebase phase) ------------

export interface ReportSchedule {
  id: string;
  name: string | null;
  frequency: "daily" | "weekly" | "monthly";
  subject_kind: "all" | "vehicle" | "driver";
  subject_id: string | null;
  types: string; // comma list
  period: string;
  email: string;
  is_active: boolean;
  created_at?: string;
}

export interface CreateScheduleInput {
  name?: string;
  frequency: "daily" | "weekly" | "monthly";
  subject_kind: "all" | "vehicle" | "driver";
  subject_id?: string | null;
  types: string[];
  period: string;
  email: string;
}

export async function listReportSchedules(): Promise<ReportSchedule[]> {
  const res = await managerFetch("/report-schedules");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load schedules."));
  return (await res.json()).schedules as ReportSchedule[];
}

export async function createReportSchedule(input: CreateScheduleInput): Promise<ReportSchedule> {
  const res = await managerFetch("/report-schedules", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create schedule."));
  return (await res.json()) as ReportSchedule;
}

export async function deleteReportSchedule(id: string): Promise<void> {
  const res = await managerFetch(`/report-schedules/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete schedule."));
}

// --- Staff users (managers / dispatchers / viewers) --------------------------

// Org staff permission flags (mirrors the backend PERMISSION_KEYS).
export const MANAGER_PERMISSIONS = [
  "manage_users",
  "manage_drivers",
  "manage_vehicles",
  "manage_devices",
  "manage_routes",
  "manage_trips",
  "manage_passengers",
  "view_tracking",
  "view_reports",
] as const;

export type StaffRole = "manager" | "dispatcher" | "viewer";

export interface ManagerUser {
  id: string;
  name: string;
  username: string;
  role: StaffRole;
  permissions: Record<string, boolean> | null;
  is_active: boolean;
  created_at?: string;
}

export async function listUsers(): Promise<ManagerUser[]> {
  const res = await managerFetch("/users");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load users."));
  return (await res.json()).users as ManagerUser[];
}

export interface CreateUserInput {
  name: string;
  username: string;
  password: string;
  role: StaffRole;
  phone?: string;
  permissions?: Record<string, boolean>;
}

export async function createUser(input: CreateUserInput): Promise<ManagerUser> {
  const res = await managerFetch("/users", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create user."));
  return (await res.json()) as ManagerUser;
}

export interface UpdateUserInput {
  name?: string;
  role?: StaffRole;
  is_active?: boolean;
  permissions?: Record<string, boolean>;
}

export async function updateUser(id: string, input: UpdateUserInput): Promise<ManagerUser> {
  const res = await managerFetch(`/users/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update user."));
  return (await res.json()) as ManagerUser;
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

// Last-known positions for ALL drivers with recent activity (online + dimmed).
export interface DriverPosition {
  driver_id: string;
  name: string | null;
  vehicle_bus_number: string | null;
  route_id: string | null;
  route_name: string | null;
  assignment_window: string | null; // "08:00–10:00" of the current assignment
  assignment_count: number; // assignments scheduled today
  position: { lat: number; lng: number; recorded_at: string } | null;
  online: boolean;
  on_trip: boolean;
  last_ended_at: string | null;
}

export async function getDriverPositions(): Promise<DriverPosition[]> {
  const res = await managerFetch("/live/positions");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load driver positions."));
  return (await res.json()).drivers as DriverPosition[];
}

// --- Org centers (university / hubs) -----------------------------------------

export interface OrgCenter {
  id: string;
  name: string;
  lat: number;
  lng: number;
  is_primary: boolean;
  created_at?: string;
}

export interface CenterInput {
  name: string;
  lat: number;
  lng: number;
  is_primary?: boolean;
}

export async function listCenters(): Promise<OrgCenter[]> {
  const res = await managerFetch("/centers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load centers."));
  return (await res.json()).centers as OrgCenter[];
}

export async function createCenter(input: CenterInput): Promise<OrgCenter> {
  const res = await managerFetch("/centers", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create center."));
  return (await res.json()) as OrgCenter;
}

export async function updateCenter(id: string, input: Partial<CenterInput>): Promise<OrgCenter> {
  const res = await managerFetch(`/centers/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update center."));
  return (await res.json()) as OrgCenter;
}

export async function deleteCenter(id: string): Promise<void> {
  const res = await managerFetch(`/centers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete center."));
}

// --- Passengers (students) ---------------------------------------------------

export interface ManagerPassenger {
  id: string;
  name: string | null;
  email: string | null;
  is_active: boolean;
  university_id: string | null;
  route_id: string | null;
  route_name: string | null;
  // School module (students):
  parent_phone?: string | null;
  parent_email?: string | null;
  student_phone?: string | null;
  grade?: string | null;
  class_name?: string | null;
}

export interface CreatePassengerInput {
  name: string;
  email: string;
  university_id?: string;
  route_id: string;
  // School module (students):
  parent_phone?: string;
  parent_email?: string;
  student_phone?: string;
  grade?: string;
  class_name?: string;
}

export interface PassengerCreateResult {
  email: string;
  default_password: string;
  must_change_password: boolean;
  parent_created?: boolean; // school: false when an existing parent was reused (siblings)
}

export async function listPassengers(): Promise<ManagerPassenger[]> {
  const res = await managerFetch("/passengers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load passengers."));
  return (await res.json()).passengers as ManagerPassenger[];
}

export async function createPassenger(input: CreatePassengerInput): Promise<PassengerCreateResult> {
  const res = await managerFetch("/passengers", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create passenger."));
  return (await res.json()) as PassengerCreateResult;
}

export interface UpdatePassengerInput {
  name?: string;
  university_id?: string | null;
  route_id?: string;
  is_active?: boolean;
  parent_phone?: string | null;
  parent_email?: string | null;
  student_phone?: string | null;
  grade?: string | null;
  class_name?: string | null;
}

export async function updatePassenger(id: string, input: UpdatePassengerInput): Promise<void> {
  const res = await managerFetch(`/passengers/${id}`, { method: "PATCH", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update passenger."));
}

export async function deletePassenger(id: string): Promise<void> {
  const res = await managerFetch(`/passengers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete passenger."));
}

export interface BulkPassengerRow {
  name: string;
  email: string;
  university_id?: string;
  route: string; // route id or name
}

export interface BulkResult {
  created: number;
  failed: number;
  errors: { row: number; error: string; label?: string }[];
}

export async function bulkCreatePassengers(rows: BulkPassengerRow[]): Promise<BulkResult> {
  const res = await managerFetch("/passengers/bulk", { method: "POST", body: JSON.stringify({ rows }) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to upload passengers."));
  return (await res.json()) as BulkResult;
}

/** Generic bulk import: POST { rows } to `path`, returns per-row results. */
export async function bulkImport(path: string, rows: Record<string, string>[]): Promise<BulkResult> {
  const res = await managerFetch(path, { method: "POST", body: JSON.stringify({ rows }) });
  if (!res.ok) throw new Error(await extractError(res, "Bulk upload failed."));
  return (await res.json()) as BulkResult;
}

// --- School settings (change-request cutoff time) ---------------------------

export async function getSchoolSettings(): Promise<{ change_cutoff_time: string }> {
  const res = await managerFetch("/school/settings");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load school settings."));
  return (await res.json()) as { change_cutoff_time: string };
}

export async function updateSchoolCutoff(change_cutoff_time: string): Promise<{ change_cutoff_time: string }> {
  const res = await managerFetch("/school/settings", { method: "PUT", body: JSON.stringify({ change_cutoff_time }) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to save the cutoff time."));
  return (await res.json()) as { change_cutoff_time: string };
}

export const bulkCreateVehicles = (rows: Record<string, string>[]) => bulkImport("/vehicles/bulk", rows);
export const bulkCreateBusDrivers = (rows: Record<string, string>[]) => bulkImport("/bus-drivers/bulk", rows);
export const bulkCreateDrivers = (rows: Record<string, string>[]) => bulkImport("/drivers/bulk", rows);
export const bulkCreateStudents = (rows: Record<string, string>[]) => bulkImport("/passengers/bulk-students", rows);

// --- Attendance reports (School module, manager-only) ------------------------

export interface AttendanceColumn {
  key: string;
  label: string;
}

export async function getAttendanceColumns(): Promise<{ columns: AttendanceColumn[]; default: string[] }> {
  const res = await managerFetch("/attendance/columns");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load columns."));
  return (await res.json()) as { columns: AttendanceColumn[]; default: string[] };
}

export interface AttendanceExportParams {
  format: "xlsx" | "pdf";
  columns: string[];
  route_id?: string;
  student_id?: string;
  date_from?: string;
  date_to?: string;
}

/** Fetch the export as a Blob and trigger a browser download. */
export async function exportAttendance(p: AttendanceExportParams): Promise<void> {
  const q = new URLSearchParams();
  q.set("format", p.format);
  if (p.columns.length) q.set("columns", p.columns.join(","));
  if (p.route_id) q.set("route_id", p.route_id);
  if (p.student_id) q.set("student_id", p.student_id);
  if (p.date_from) q.set("date_from", p.date_from);
  if (p.date_to) q.set("date_to", p.date_to);

  const res = await managerFetch(`/attendance/export?${q.toString()}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to export attendance."));
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `attendance.${p.format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

// --- History (trips + pings over a range) ------------------------------------

export interface HistoryPing {
  lat: number;
  lng: number;
  recorded_at: string;
}

export interface HistoryStopVisit {
  stop_id: string | null;
  stop_name: string | null;
  stop_order: number | null;
  arrival_time: string | null;
  departure_time: string | null;
  planned_dwell_seconds: number | null;
  actual_dwell_seconds: number | null;
}

export interface HistoryTrip {
  trip_id: string;
  driver_id: string | null;
  driver_name: string | null;
  vehicle_id: string | null;
  vehicle_bus_number: string | null;
  route_id: string | null;
  route_name: string | null;
  route_geometry: { type: "LineString"; coordinates: [number, number][] } | null;
  route_color: string | null;
  status: string | null;
  started_at: string | null;
  ended_at: string | null;
  pings: HistoryPing[];
  stop_visits: HistoryStopVisit[];
}

export interface HistoryParams {
  kind: "drivers" | "vehicles";
  subject_id?: string;
  date_from?: string;
  date_to?: string;
}

export async function getHistory(params: HistoryParams): Promise<HistoryTrip[]> {
  const p = new URLSearchParams();
  p.set("kind", params.kind);
  if (params.subject_id) p.set("subject_id", params.subject_id);
  if (params.date_from) p.set("date_from", params.date_from);
  if (params.date_to) p.set("date_to", params.date_to);
  const res = await managerFetch(`/history?${p.toString()}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to load history."));
  return (await res.json()).trips as HistoryTrip[];
}

// --- Driver groups (persistent, nestable) -----------------------------------

export interface GroupDriver {
  driver_id: string;
  name: string | null;
}

export interface DriverGroup {
  id: string;
  name: string;
  parent_group_id: string | null;
  drivers: GroupDriver[];
  children: DriverGroup[];
}

export async function listDriverGroups(): Promise<DriverGroup[]> {
  const res = await managerFetch("/driver-groups");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load groups."));
  return (await res.json()).groups as DriverGroup[];
}

export async function createDriverGroup(name: string, parentGroupId?: string | null): Promise<void> {
  const res = await managerFetch("/driver-groups", {
    method: "POST",
    body: JSON.stringify({ name, parent_group_id: parentGroupId ?? null }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create group."));
}

export async function updateDriverGroup(
  id: string,
  patch: { name?: string; parent_group_id?: string | null },
): Promise<void> {
  const res = await managerFetch(`/driver-groups/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update group."));
}

export async function deleteDriverGroup(id: string): Promise<void> {
  const res = await managerFetch(`/driver-groups/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete group."));
}

export async function addDriverToGroup(groupId: string, driverId: string): Promise<void> {
  const res = await managerFetch(`/driver-groups/${groupId}/members`, {
    method: "POST",
    body: JSON.stringify({ driver_id: driverId }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to add driver to group."));
}

export async function removeDriverFromGroup(groupId: string, driverId: string): Promise<void> {
  const res = await managerFetch(`/driver-groups/${groupId}/members/${driverId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to remove driver from group."));
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

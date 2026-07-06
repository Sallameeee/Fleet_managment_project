// API client for the FastAPI backend.
//
// Token storage: we keep the access token in memory (module variable) AND mirror
// it to localStorage so a page refresh stays logged in.
//
// TRADEOFF: localStorage is readable by any JavaScript on the page, so a
// successful XSS attack could steal the token. It's the simplest thing that
// survives refreshes and is fine for local/dev. SAFER FOR LATER: have the
// backend set an HttpOnly, Secure, SameSite cookie (not reachable from JS) and
// stop storing the token in JS at all — that defeats token theft via XSS.

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "fleet_admin_token";

let inMemoryToken: string | null = null;

export function getToken(): string | null {
  if (inMemoryToken) return inMemoryToken;
  if (typeof window !== "undefined") {
    inMemoryToken = window.localStorage.getItem(TOKEN_KEY);
  }
  return inMemoryToken;
}

export function setToken(token: string): void {
  inMemoryToken = token;
  if (typeof window !== "undefined") {
    window.localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearToken(): void {
  inMemoryToken = null;
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(TOKEN_KEY);
  }
}

export function isAuthenticated(): boolean {
  return getToken() !== null;
}

export function logout(): void {
  clearToken();
}

export interface SuperAdmin {
  id: string;
  name: string;
  email: string;
  permissions?: Record<string, boolean>;
  is_active?: boolean;
}

/** Platform permission gate (UI). view_all is the root bypass. */
export function canSuper(
  admin: { permissions?: Record<string, boolean> } | null,
  perm: string,
): boolean {
  const p = admin?.permissions ?? {};
  return p.view_all === true || p[perm] === true;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
  admin: SuperAdmin;
}

/**
 * Authenticate a PLATFORM super admin by email + password.
 *
 * Calls POST /auth/super-admin/login, which authenticates the email against
 * Supabase Auth and verifies the user is in the super_admins table. Any failure
 * (wrong password OR a valid account that isn't a super admin) returns the SAME
 * generic 401, so the panel never reveals account existence or admin status.
 */
export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_URL}/auth/super-admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!res.ok) {
    let detail = "Invalid email or password.";
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body; keep default */
    }
    throw new Error(detail);
  }

  const data = (await res.json()) as LoginResponse;
  setToken(data.access_token);
  return data;
}

/** fetch() wrapper that attaches the Bearer token to authenticated requests. */
export async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`${API_URL}${path}`, { ...options, headers });
}

/**
 * Server-verified guard: confirms the stored token is still a valid super-admin
 * token. Resolves to the admin on success; throws on 401/any failure so callers
 * can clear the token and bounce to /login.
 */
export async function fetchSuperAdminMe(): Promise<SuperAdmin> {
  const res = await apiFetch("/auth/super-admin/me");
  if (!res.ok) {
    throw new Error("Not authorized");
  }
  return (await res.json()) as SuperAdmin;
}

/** Pull a human-readable message out of a failed JSON response. */
async function extractError(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

// --- Organizations -----------------------------------------------------------

export interface OrgCounts {
  profiles: number;
  drivers: number;
  vehicles: number;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  module?: string; // 'university' (default) | 'school'
  max_devices: number;
  monthly_fee: number;
  subscription_expiry: string | null;
  created_at: string;
  counts?: OrgCounts;
}

export async function listOrganizations(): Promise<Organization[]> {
  const res = await apiFetch("/organizations");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load organizations."));
  const data = await res.json();
  return data.organizations as Organization[];
}

export interface CreateOrgInput {
  name: string;
  username: string;
  password: string;
  address?: string;
  email?: string;
  phone?: string;
  plan: "basic" | "pro" | "enterprise";
  module: "university" | "school";
  max_devices: number;
  monthly_fee: number;
  subscription_expiry?: string | null;
}

export interface CreateOrgResult {
  status: string;
  message: string;
  organization: Organization;
  owner: {
    id: string;
    username: string;
    login: string; // username@org-slug — hand this to the client
    login_email: string;
    role: string;
  };
}

export async function createOrganization(input: CreateOrgInput): Promise<CreateOrgResult> {
  const res = await apiFetch("/organizations", {
    method: "POST",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create organization."));
  return (await res.json()) as CreateOrgResult;
}

export interface OrgProfile {
  id: string;
  name: string;
  username: string;
  role: string;
  is_active: boolean;
}

export interface OrgVehicle {
  id: string;
  bus_number: string;
  plate_number: string | null;
  is_active: boolean;
}

export interface OrganizationDetail extends Organization {
  address?: string | null;
  email?: string | null;
  phone?: string | null;
  profiles: OrgProfile[];
  vehicles: OrgVehicle[];
}

export async function getOrganization(id: string): Promise<OrganizationDetail> {
  const res = await apiFetch(`/organizations/${id}`);
  if (!res.ok) throw new Error(await extractError(res, "Failed to load organization."));
  return (await res.json()) as OrganizationDetail;
}

export interface OrgPatch {
  plan?: "basic" | "pro" | "enterprise";
  module?: "university" | "school";
  max_devices?: number;
  monthly_fee?: number;
  subscription_expiry?: string | null;
  address?: string;
  email?: string;
  phone?: string;
}

export async function updateOrganization(id: string, patch: OrgPatch): Promise<Organization> {
  const res = await apiFetch(`/organizations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update organization."));
  return (await res.json()) as Organization;
}

export async function setOrganizationStatus(
  id: string,
  status: "active" | "suspended" | "expired",
): Promise<Organization> {
  const res = await apiFetch(`/organizations/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(await extractError(res, "Failed to change status."));
  return (await res.json()) as Organization;
}

// --- Finance -----------------------------------------------------------------

export interface FinanceRow {
  id: string;
  name: string;
  status: string;
  monthly_fee: number;
  subscription_expiry: string | null;
  expected: number;
  collected: number;
  outstanding: number;
}

export interface FinanceSummary {
  totals: { expected: number; collected: number; outstanding: number };
  organizations: FinanceRow[];
}

export async function getFinance(): Promise<FinanceSummary> {
  const res = await apiFetch("/finance");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load finance summary."));
  return (await res.json()) as FinanceSummary;
}

// --- Platform vehicles -------------------------------------------------------

export interface AdminVehicle {
  id: string;
  org_id: string;
  org_name: string | null;
  bus_number: string;
  plate_number: string | null;
  is_active: boolean;
  has_active_trip: boolean;
}

export async function listAllVehicles(): Promise<AdminVehicle[]> {
  const res = await apiFetch("/admin/vehicles");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load vehicles."));
  const data = await res.json();
  return data.vehicles as AdminVehicle[];
}

export interface AdminDriver {
  id: string;
  name: string;
  username: string;
  org_id: string;
  org_name: string | null;
  is_active: boolean;
  online: boolean;
  current_vehicle: string | null;
}

export async function listAllDrivers(): Promise<AdminDriver[]> {
  const res = await apiFetch("/admin/drivers");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load drivers."));
  const data = await res.json();
  return data.drivers as AdminDriver[];
}

// --- Org destructive / impersonation -----------------------------------------

export interface DeleteOrgResult {
  status: string;
  organization: { id: string; name: string };
  deleted: Record<string, number>;
}

export async function deleteOrganization(id: string): Promise<DeleteOrgResult> {
  const res = await apiFetch(`/organizations/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete organization."));
  return (await res.json()) as DeleteOrgResult;
}

export interface ImpersonateResult {
  access_token: string;
  token_type: string;
  expires_in: number;
  impersonation: {
    org_id: string;
    org_name: string;
    org_slug: string;
    owner_name: string;
    by: string;
  };
}

export async function impersonateOrganization(id: string): Promise<ImpersonateResult> {
  const res = await apiFetch(`/organizations/${id}/impersonate`, { method: "POST" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to start impersonation."));
  return (await res.json()) as ImpersonateResult;
}

// --- Platform staff (super-admin-panel users) --------------------------------

export const PLATFORM_PERMISSIONS = [
  "manage_orgs",
  "manage_orgs_status",
  "view_finance",
  "manage_platform_users",
  "view_all",
] as const;

export interface PlatformUser {
  id: string;
  name: string;
  email: string;
  permissions: Record<string, boolean>;
  is_active: boolean;
  created_at?: string;
}

export async function listPlatformUsers(): Promise<PlatformUser[]> {
  const res = await apiFetch("/admin/users");
  if (!res.ok) throw new Error(await extractError(res, "Failed to load platform users."));
  const data = await res.json();
  return data.users as PlatformUser[];
}

export interface CreatePlatformUserInput {
  name: string;
  email: string;
  password: string;
  permissions: Record<string, boolean>;
}

export async function createPlatformUser(input: CreatePlatformUserInput): Promise<PlatformUser> {
  const res = await apiFetch("/admin/users", { method: "POST", body: JSON.stringify(input) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to create platform user."));
  return (await res.json()) as PlatformUser;
}

export interface UpdatePlatformUserInput {
  name?: string;
  permissions?: Record<string, boolean>;
  is_active?: boolean;
}

export async function updatePlatformUser(
  id: string,
  patch: UpdatePlatformUserInput,
): Promise<PlatformUser> {
  const res = await apiFetch(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  if (!res.ok) throw new Error(await extractError(res, "Failed to update platform user."));
  return (await res.json()) as PlatformUser;
}

export async function deletePlatformUser(id: string): Promise<void> {
  const res = await apiFetch(`/admin/users/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await extractError(res, "Failed to delete platform user."));
}

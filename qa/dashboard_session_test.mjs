// Multi-tab test of the dashboard's REAL session module (fleet-dashboard/lib/session.ts).
//
// Every "tab" is a separate instance of the module (its own in-flight promise and
// backoff), all sharing one localStorage, one Web Locks manager and one server,
// exactly like same-origin browser tabs.
//
//   node --experimental-transform-types qa/dashboard_session_test.mjs          (fake server)
//   node --experimental-transform-types qa/dashboard_session_test.mjs --live   (live backend, qa_ accounts)
//
// The fake server follows Supabase's rotation as measured live (qa/auth_probe.py):
// active token rotates; its parent returns the current pair; older -> 401
// refresh_token_already_used; parallel refreshes of one token share one pair.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const LIVE = process.argv.includes("--live");
const SESSION = pathToFileURL(join(HERE, "..", "fleet-dashboard", "lib", "session.ts")).href;
const FAILS = [];
const check = (name, ok, ev = "") => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}  ${ev}`);
  if (!ok) FAILS.push(name);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- shared browser-like globals -------------------------------------------------
const store = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
  addEventListener: () => {},
};
class FakeLocks {
  constructor() {
    this.tails = new Map();
    this.inside = 0;
    this.maxInside = 0;
  }
  request(name, _opts, fn) {
    const prev = this.tails.get(name) || Promise.resolve();
    let release;
    const mine = new Promise((r) => (release = r));
    this.tails.set(name, prev.then(() => mine));
    return prev.then(async () => {
      this.inside++;
      this.maxInside = Math.max(this.maxInside, this.inside);
      try {
        return await fn();
      } finally {
        this.inside--;
        release();
      }
    });
  }
}
const locks = new FakeLocks();
let useLocks = true;
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  get: () => (useLocks ? { locks } : {}),
});

let tabSeq = 0;
async function openTabs(n) {
  const tabs = [];
  for (let i = 0; i < n; i++) tabs.push(await import(`${SESSION}?tab=${++tabSeq}`));
  return tabs;
}

// ---- fake server -------------------------------------------------------------------
class Chain {
  constructor(prefix) {
    this.p = prefix;
    this.chain = [`${prefix}R0`];
    this.valid = new Set([`${prefix}A0`]);
    this.calls = 0;
    this.already = 0;
    this.mode = "ok";
  }
  get active() {
    return this.chain[this.chain.length - 1];
  }
  access(rt) {
    return rt.replace("R", "A");
  }
  async refresh(rt) {
    this.calls++;
    await sleep(Math.random() * 15);
    if (this.mode === "offline") throw new TypeError("fetch failed");
    if (this.mode === "down") return [503, { detail: "unavailable", code: "auth_unavailable" }];
    if (this.mode === "gone") return [401, { detail: "Could not refresh session.", code: "session_not_found" }];
    if (this.mode === "deactivated") return [403, { detail: "This account has been deactivated. Contact your administrator." }];
    const i = this.chain.indexOf(rt);
    if (rt === this.active) {
      this.chain.push(`${this.p}R${this.chain.length}`);
      this.valid.add(this.access(this.active));
    } else if (i !== this.chain.length - 2) {
      if (i >= 0) this.already++;
      return [401, { detail: "Could not refresh session.", code: i >= 0 ? "refresh_token_already_used" : "refresh_token_not_found" }];
    }
    return [200, { access_token: this.access(this.active), refresh_token: this.active }];
  }
  expireAll() {
    this.valid.clear();
  }
}
let mgr, adm, impSeq;
function resetFake() {
  store.clear();
  mgr = new Chain("M");
  adm = new Chain("S");
  impSeq = 0;
  store.set("fleet_manager_token", "MA0");
  store.set("fleet_manager_refresh", "MR0");
  store.set("fleet_admin_token", "SA0");
  store.set("fleet_admin_refresh", "SR0");
}
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
async function fakeFetch(url, opts = {}) {
  const path = new URL(url).pathname;
  const auth = new Headers(opts.headers).get("Authorization")?.replace("Bearer ", "") ?? "";
  if (path === "/auth/refresh") {
    const rt = JSON.parse(opts.body).refresh_token;
    const chain = rt.startsWith("S") ? adm : mgr;
    const [status, body] = await chain.refresh(rt);
    return json(status, body);
  }
  if (path.startsWith("/organizations/") && path.endsWith("/impersonate")) {
    if (!adm.valid.has(auth)) return json(401, { detail: "expired" });
    const tok = `IMP${++impSeq}`;
    mgr.valid.add(tok);
    return json(200, { access_token: tok });
  }
  const chain = auth.startsWith("S") ? adm : mgr;
  if (path === "/deactivated") return json(403, { detail: "This account has been deactivated. Contact your administrator." });
  return chain.valid.has(auth) ? json(200, { id: "u1", path }) : json(401, { detail: "Invalid or expired token" });
}

async function fakeSuite() {
  globalThis.fetch = fakeFetch;
  process.env.NEXT_PUBLIC_API_URL = "http://fake.local";

  // 1. Five tabs, the token expires, all five call at once; 60 rounds.
  resetFake();
  let tabs = await openTabs(5);
  let all200 = true;
  for (let round = 0; round < 60; round++) {
    mgr.expireAll();
    const res = await Promise.all(tabs.map(async (t) => {
      await sleep(Math.random() * 5);
      return t.authFetch("manager", "/x");
    }));
    all200 &&= res.every((r) => r.status === 200);
  }
  check("5 tabs x 60 expiries: every call 200", all200);
  check("  exactly ONE refresh per expiry (Web Locks + adopt)", mgr.calls === 60, `refresh calls=${mgr.calls}`);
  check("  never refresh_token_already_used", mgr.already === 0, `already_used=${mgr.already}`);
  check("  lock never held by two tabs", locks.maxInside === 1, `maxInside=${locks.maxInside}`);
  check("  stored refresh token = server's active token", store.get("fleet_manager_refresh") === mgr.active);

  // 2. Same without Web Locks (old browser): still no failure.
  resetFake();
  useLocks = false;
  tabs = await openTabs(5);
  all200 = true;
  for (let round = 0; round < 40; round++) {
    mgr.expireAll();
    const res = await Promise.all(tabs.map((t) => t.authFetch("manager", "/x")));
    all200 &&= res.every((r) => r.status === 200);
  }
  useLocks = true;
  check("no Web Locks: 5 tabs x 40 expiries still all 200", all200, `refresh calls=${mgr.calls} already_used=${mgr.already}`);

  // 3. A tab left behind with an old access token adopts the new one (no network).
  resetFake();
  tabs = await openTabs(2);
  mgr.expireAll();
  await tabs[0].authFetch("manager", "/x");
  const before = mgr.calls;
  const out = await tabs[1].refreshSession("manager", "MA0");
  check("stale tab adopts the token another tab renewed", out === "adopted" && mgr.calls === before, `outcome=${out}`);

  // 4. Offline / 503 during renewal: kept, no reauth, recovers.
  resetFake();
  tabs = await openTabs(1);
  const events = [];
  tabs[0].onSessionState((k, s) => events.push(`${k}:${s}`));
  mgr.expireAll();
  mgr.mode = "offline";
  let r = await tabs[0].authFetch("manager", "/x");
  check("offline renewal: caller gets 401, session KEPT, no re-auth", r.status === 401 && store.get("fleet_manager_refresh") === "MR0" && !events.includes("manager:reauth"));
  mgr.mode = "down";
  r = await tabs[0].authFetch("manager", "/x");
  check("503 renewal (inside backoff): nothing sent, session kept", r.status === 401 && store.get("fleet_manager_token") === "MA0", `calls=${mgr.calls}`);
  mgr.mode = "ok";
  const t2 = (await openTabs(1))[0]; // a fresh tab has no backoff: proves the session itself is fine
  r = await t2.authFetch("manager", "/x");
  check("network back: renewal works with the kept token", r.status === 200);

  // 5. Session deleted on the server: reauth event, tokens kept, no hammering, re-login heals every tab.
  resetFake();
  tabs = await openTabs(3);
  const ev = [];
  tabs.forEach((t, i) => t.onSessionState((k, s) => ev.push(`${i}:${k}:${s}`)));
  mgr.expireAll();
  mgr.mode = "gone";
  const rs = await Promise.all(tabs.map((t) => t.authFetch("manager", "/x")));
  const callsDead = mgr.calls;
  for (let i = 0; i < 5; i++) await tabs[0].authFetch("manager", "/x");
  check("session gone: every tab gets 'reauth'", ev.filter((e) => e.endsWith("manager:reauth")).length >= 1 && rs.every((x) => x.status === 401), ev.join(","));
  check("  tokens NOT cleared", store.get("fleet_manager_token") === "MA0" && store.get("fleet_manager_refresh") === "MR0");
  check("  no refresh hammering while dead", mgr.calls === callsDead, `calls ${callsDead} -> ${mgr.calls}`);
  mgr.mode = "ok";
  mgr.valid.add("MA-new");
  tabs[1].storeSession("manager", "MA-new", "MR-new"); // the user re-enters the password in tab 1
  const healed = await Promise.all(tabs.map((t) => t.authFetch("manager", "/x")));
  check("  re-login in one tab heals all tabs", healed.every((x) => x.status === 200));

  // 6. Deactivated: forbidden event (the one allowed sign-out).
  resetFake();
  tabs = await openTabs(1);
  const ev6 = [];
  tabs[0].onSessionState((k, s) => ev6.push(`${k}:${s}`));
  mgr.expireAll();
  mgr.mode = "deactivated";
  await tabs[0].authFetch("manager", "/x");
  await tabs[0].authFetch("manager", "/deactivated");
  check("deactivated: 'forbidden' raised", ev6.includes("manager:forbidden"), ev6.join(","));

  // 7. Super admin past the 1-hour token: renews like everyone else.
  resetFake();
  tabs = await openTabs(3);
  adm.expireAll();
  const ar = await Promise.all(tabs.map((t) => t.authFetch("admin", "/auth/super-admin/me")));
  check("super admin: expired token renews silently in 3 tabs", ar.every((x) => x.status === 200) && adm.calls === 1, `calls=${adm.calls}`);

  // 8. Impersonated org view (30-min token, no refresh token) renews via the admin session.
  resetFake();
  store.delete("fleet_manager_refresh");
  store.set("fleet_manager_token", "IMP0");
  store.set("fleet_manager_impersonation", JSON.stringify({ org_id: "org-1", org_name: "qa_ Org" }));
  adm.expireAll(); // ...and the admin token expired too
  tabs = await openTabs(2);
  const ir = await Promise.all(tabs.map((t) => t.authFetch("manager", "/x")));
  check("impersonation: renewed through the (also renewed) super-admin session", ir.every((x) => x.status === 200) && impSeq === 1, `impersonate calls=${impSeq} admin refreshes=${adm.calls}`);

  // 9. fetchMe classification (what the layouts act on).
  resetFake();
  tabs = await openTabs(1);
  const reason = async () => {
    try {
      await tabs[0].fetchMe("manager", "/auth/me");
      return "ok";
    } catch (e) {
      return e.reason;
    }
  };
  check("fetchMe ok", (await reason()) === "ok");
  mgr.expireAll();
  mgr.mode = "gone";
  check("fetchMe: dead session -> 'auth' (modal, not logout)", (await reason()) === "auth");
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };
  check("fetchMe: offline -> 'network' (keep + retry)", (await reason()) === "network");
  globalThis.fetch = async () => json(502, { detail: "bad gateway" });
  check("fetchMe: 502 -> 'server' (keep + retry)", (await reason()) === "server");
  globalThis.fetch = async () => json(403, { detail: "This account has been deactivated. Contact your administrator." });
  check("fetchMe: 403 -> 'forbidden' (sign out)", (await reason()) === "forbidden");
}

// ---- live backend (qa_ accounts only) ------------------------------------------------------
async function liveSuite() {
  const base = process.env.QA_BASE || "https://fleetmanagmentproject-production-1bd7d791.up.railway.app";
  process.env.NEXT_PUBLIC_API_URL = base;
  const realFetch = globalThis.fetch;
  let refreshCalls = 0;
  const statuses = [];
  globalThis.fetch = (url, opts) => {
    if (String(url).endsWith("/auth/refresh")) refreshCalls++;
    return realFetch(url, opts).then((r) => {
      if (String(url).endsWith("/auth/refresh")) statuses.push(r.status);
      return r;
    });
  };
  const ledger = JSON.parse(readFileSync(join(HERE, "out", process.env.QA_AUTH_LEDGER || "auth_ledger.json"), "utf8"));
  for (const acct of ledger.dashboard_accounts) {
    store.clear();
    refreshCalls = 0;
    statuses.length = 0;
    const loginPath = acct.kind === "admin" ? "/auth/super-admin/login" : "/auth/login";
    const body = acct.kind === "admin" ? { email: acct.login, password: acct.password } : { login: acct.login, password: acct.password };
    const lr = await realFetch(base + loginPath, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const ld = await lr.json();
    if (!ld.refresh_token) {
      check(`[live] ${acct.label}: login returns a refresh token`, false, `HTTP ${lr.status}`);
      continue;
    }
    const tok = acct.kind === "admin" ? "fleet_admin_token" : "fleet_manager_token";
    const ref = acct.kind === "admin" ? "fleet_admin_refresh" : "fleet_manager_refresh";
    store.set(tok, ld.access_token);
    store.set(ref, ld.refresh_token);
    const me = acct.kind === "admin" ? "/auth/super-admin/me" : "/auth/me";
    const tabs = await openTabs(6);
    let ok = true;
    const rounds = 8;
    for (let round = 0; round < rounds; round++) {
      store.set(tok, `qa-expired-${round}`); // force "expiry": the API rejects it with 401
      const res = await Promise.all(tabs.map((t) => t.authFetch(acct.kind, me)));
      ok &&= res.every((r) => r.status === 200);
    }
    check(`[live] ${acct.label}: 6 tabs x ${rounds} forced expiries, every call 200`, ok);
    check(`[live] ${acct.label}:   one real refresh per expiry, all 200`, refreshCalls === rounds && statuses.every((s) => s === 200), `refresh calls=${refreshCalls} statuses=${[...new Set(statuses)]}`);
  }
  globalThis.fetch = realFetch;
}

if (LIVE) await liveSuite();
else await fakeSuite();
console.log(`DASHBOARD SESSION ${LIVE ? "LIVE " : ""}${FAILS.length ? "FAIL" : "PASS"} (${FAILS.length} failed${FAILS.length ? ": " + FAILS.join("; ") : ""})`);
process.exit(FAILS.length ? 1 : 0);

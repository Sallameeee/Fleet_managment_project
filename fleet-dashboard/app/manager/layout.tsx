"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  managerMe,
  managerLogout,
  managerGetToken,
  cachedManagerProfile,
  onSessionState,
  SessionError,
  canAccess,
  getManagerSlug,
  getImpersonation,
  getUnreadAlertCount,
  type ManagerProfile,
  type Impersonation,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { ModuleProvider } from "@/lib/module";
import Sidebar, { type NavItem } from "@/components/Sidebar";
import ThemeToggle from "@/components/ThemeToggle";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import NotificationBell from "@/components/NotificationBell";
import ReauthModal, { SessionStrip } from "@/components/ReauthModal";

// Each nav item: href, translation key, required permission, and — for TOGGLEABLE
// features — a `feature` key. Items with no `feature` are CORE (always shown when
// the permission passes). `schoolOnly` is kept as belt-and-suspenders alongside the
// feature gate (a school feature can never surface for a university org either way).
const MANAGER_NAV: { href: string; key: string; perm?: string; group: string; schoolOnly?: boolean; feature?: string }[] = [
  { href: "/manager", key: "nav.dashboard", group: "nav.grpMonitoring" },
  { href: "/manager/full-view", key: "nav.fullView", perm: "view_tracking", group: "nav.grpMonitoring" },
  { href: "/manager/history", key: "nav.history", perm: "view_tracking", group: "nav.grpMonitoring" },
  { href: "/manager/performance", key: "nav.performance", perm: "view_tracking", group: "nav.grpMonitoring", schoolOnly: true, feature: "performance" },
  { href: "/manager/logs", key: "nav.logs", perm: "view_tracking", group: "nav.grpMonitoring", schoolOnly: true, feature: "logs" },
  { href: "/manager/buses-today", key: "nav.busesToday", perm: "view_tracking", group: "nav.grpMonitoring", schoolOnly: true, feature: "buses_today" },
  { href: "/manager/drivers", key: "nav.drivers", perm: "manage_drivers", group: "nav.grpManagement" },
  { href: "/manager/bus-drivers", key: "nav.busDrivers", perm: "manage_drivers", group: "nav.grpManagement", schoolOnly: true, feature: "bus_drivers" },
  { href: "/manager/directory", key: "nav.directory", perm: "manage_drivers", group: "nav.grpManagement", schoolOnly: true, feature: "directory" },
  { href: "/manager/vehicles", key: "nav.vehicles", perm: "manage_vehicles", group: "nav.grpManagement" },
  { href: "/manager/routes", key: "nav.routes", perm: "manage_routes", group: "nav.grpManagement" },
  { href: "/manager/assignments", key: "nav.assignments", perm: "manage_trips", group: "nav.grpManagement" },
  { href: "/manager/passengers", key: "nav.passengers", perm: "manage_passengers", group: "nav.grpManagement", feature: "passengers" },
  { href: "/manager/parents", key: "nav.parents", perm: "manage_passengers", group: "nav.grpManagement", schoolOnly: true, feature: "parents_page" },
  { href: "/manager/alerts", key: "nav.alerts", perm: "manage_trips", group: "nav.grpOperations", feature: "alerts" },
  { href: "/manager/logs/settings", key: "nav.editLogs", perm: "manage_settings", group: "nav.grpOperations", feature: "alerts" },
  // Admin-only trip decision log (both modules; the backend scopes it to the org).
  { href: "/manager/trip-debug", key: "nav.tripDebug", perm: "manage_settings", group: "nav.grpOperations" },
  { href: "/manager/change-requests", key: "nav.changeRequests", perm: "manage_passengers", group: "nav.grpOperations", schoolOnly: true, feature: "change_requests" },
  { href: "/manager/profile-requests", key: "nav.profileRequests", perm: "manage_passengers", group: "nav.grpOperations", schoolOnly: true, feature: "profile_requests" },
  { href: "/manager/parent-reports", key: "nav.parentReports", perm: "manage_passengers", group: "nav.grpOperations", feature: "reports" },
  { href: "/manager/attendance", key: "nav.attendance", perm: "manage_passengers", group: "nav.grpOperations", schoolOnly: true, feature: "attendance" },
  { href: "/manager/reports", key: "nav.reports", perm: "view_reports", group: "nav.grpOperations" },
  { href: "/manager/settings", key: "nav.settings", perm: "manage_settings", group: "nav.grpOperations" },
];

export default function ManagerLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { t } = useT();
  const [profile, setProfile] = useState<ManagerProfile | null>(null);
  const [impersonation, setImpersonation] = useState<Impersonation | null>(null);
  const [unread, setUnread] = useState(0);
  const [navOpen, setNavOpen] = useState(false); // mobile off-canvas nav

  // Settings → logo upload announces the new logo so the sidebar updates at once.
  useEffect(() => {
    function onBrand(e: Event) {
      const url = (e as CustomEvent<string | null>).detail ?? null;
      setProfile((p) => (p ? { ...p, org_logo_url: url } : p));
    }
    window.addEventListener("routemind:branding", onBrand);
    return () => window.removeEventListener("routemind:branding", onBrand);
  }, []);

  useEffect(() => {
    setImpersonation(getImpersonation());
  }, []);

  // The session never ends by itself. Network errors and server restarts keep the
  // (cached) dashboard on screen and retry; a session the server no longer knows
  // asks for the password in a modal; only a deactivated account signs out.
  const [conn, setConn] = useState<"ok" | "offline">("ok");
  const [reauth, setReauth] = useState(false);
  const [reauthOpen, setReauthOpen] = useState(false);
  const reload = useRef<() => void>(() => {});

  useEffect(() => {
    if (!managerGetToken()) {
      router.replace("/org-login"); // never signed in in this browser
      return;
    }
    let active = true;
    let needsReload = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let delay = 2000;
    const cached = cachedManagerProfile();
    if (cached) setProfile(cached);
    const signOut = () => {
      managerLogout();
      router.replace("/org-login");
    };
    const askPassword = () => {
      needsReload = true;
      setReauth(true);
      setReauthOpen(true);
    };
    const load = () => {
      if (timer) clearTimeout(timer);
      managerMe()
        .then((p) => {
          if (!active) return;
          setProfile(p);
          setConn("ok");
          setReauth(false);
          setReauthOpen(false);
          needsReload = false;
          delay = 2000;
        })
        .catch((e: unknown) => {
          if (!active) return;
          const reason = e instanceof SessionError ? e.reason : "network";
          if (reason === "forbidden") return signOut();
          if (reason === "auth") return askPassword();
          setConn("offline");
          timer = setTimeout(load, delay);
          delay = Math.min(delay * 2, 60000);
        });
    };
    reload.current = () => {
      delay = 2000;
      load();
    };
    load();
    const off = onSessionState((kind, state) => {
      if (kind !== "manager" || !active) return;
      if (state === "reauth") askPassword();
      else if (state === "forbidden") signOut();
      else if (state === "signedOut") router.replace("/org-login"); // Sign out pressed in another tab
      else if (state === "ok") {
        setReauth(false);
        setReauthOpen(false);
        if (needsReload) load(); // signed in again (here or in another tab)
      }
    });
    const onOnline = () => reload.current();
    window.addEventListener("online", onOnline);
    return () => {
      active = false;
      off();
      if (timer) clearTimeout(timer);
      window.removeEventListener("online", onOnline);
    };
  }, [router]);

  const refreshUnread = useCallback(() => {
    getUnreadAlertCount().then(setUnread).catch(() => {});
  }, []);

  useEffect(() => {
    if (!profile) return;
    refreshUnread();
    // Refresh when the alerts page marks something read, and periodically.
    const onChange = () => refreshUnread();
    window.addEventListener("fleet:alerts-changed", onChange);
    const id = setInterval(refreshUnread, 30000);
    return () => {
      window.removeEventListener("fleet:alerts-changed", onChange);
      clearInterval(id);
    };
  }, [profile, refreshUnread]);

  if (!profile) {
    // Nothing cached yet (first visit in this browser): still never a sign-out.
    if (reauth && impersonation)
      return (
        <div className="flex min-h-screen items-start justify-center pt-24">
          <div className="w-full max-w-lg space-y-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-100">
            <p>{t("session.impEnded")}</p>
            <button
              onClick={() => {
                managerLogout();
                router.replace("/dashboard");
              }}
              className="rounded-md border border-amber-500/50 px-3 py-1 font-medium hover:bg-amber-500/20"
            >
              {t("session.backToPlatform")}
            </button>
          </div>
        </div>
      );
    if (reauth)
      return (
        <div className="min-h-screen">
          <ReauthModal
            kind="manager"
            open
            slug={getManagerSlug()}
            onDone={() => reload.current()}
            onLater={() => reload.current()}
          />
        </div>
      );
    if (conn === "offline")
      return (
        <div className="flex min-h-screen items-start justify-center pt-24">
          <div className="w-full max-w-lg">
            <SessionStrip state="offline" onRetry={() => reload.current()} />
          </div>
        </div>
      );
    return null;
  }

  const module = profile.module ?? "university";
  const isSchool = module === "school";

  // Enabled feature gate. undefined (old backend / not yet deployed) → all-on, so
  // the nav never silently loses items before the flags feature is live.
  const enabled = profile.enabled_features;
  const featureOn = (f?: string) => !f || !enabled || enabled.includes(f);

  const items: NavItem[] = MANAGER_NAV.filter(
    (n) => (!n.perm || canAccess(profile, n.perm)) && (!n.schoolOnly || isSchool) && featureOn(n.feature),
  ).map((n) => ({
    href: n.href,
    // School orgs reuse the SAME systems, just relabelled.
    label: isSchool && n.key === "nav.drivers" ? t("nav.supervisors")
      : isSchool && n.key === "nav.passengers" ? t("nav.students")
      : !isSchool && n.key === "nav.parentReports" ? t("nav.studentReports")
      : t(n.key),
    badge: n.href === "/manager/alerts" ? unread : undefined,
    group: t(n.group),
  }));

  const slug = getManagerSlug();

  function handleLogout() {
    managerLogout();
    router.replace("/org-login");
  }

  function exitImpersonation() {
    managerLogout();
    router.replace("/dashboard");
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar
        title={t("login.managerTitle")}
        items={items}
        open={navOpen}
        onClose={() => setNavOpen(false)}
        orgName={profile?.org_name ?? null}
        logoUrl={profile?.org_logo_url ?? null}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        {conn === "offline" && <SessionStrip state="offline" onRetry={() => reload.current()} />}
        {reauth && !impersonation && !reauthOpen && (
          <SessionStrip state="reauth" onSignIn={() => setReauthOpen(true)} />
        )}
        {reauth && impersonation && (
          <div
            role="status"
            className="flex items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-100 md:px-6"
          >
            <span>{t("session.impEnded")}</span>
            <button
              onClick={exitImpersonation}
              className="shrink-0 rounded-md border border-amber-500/50 px-3 py-1 font-medium hover:bg-amber-500/20"
            >
              {t("session.backToPlatform")}
            </button>
          </div>
        )}
        <ReauthModal
          kind="manager"
          open={reauth && reauthOpen && !impersonation}
          identity={profile.username ?? null}
          slug={slug}
          expectedId={profile.id}
          onDone={() => reload.current()}
          onLater={() => setReauthOpen(false)}
        />
        {impersonation && (
          <div className="flex items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-6 py-2 text-sm text-amber-200">
            <span>
              {t("header.viewingAs")} <strong>{impersonation.org_name}</strong> ({t("header.impersonation")})
            </span>
            <button
              onClick={exitImpersonation}
              className="rounded-md border border-amber-500/50 px-3 py-1 font-medium text-amber-100 hover:bg-amber-500/20"
            >
              {t("header.exitImpersonation")}
            </button>
          </div>
        )}
        <header className="flex items-center gap-3 border-b border-ink-800 px-4 py-3 text-sm md:px-6">
          {/* Mobile: hamburger to open the off-canvas nav. Hidden at md+. */}
          <button
            onClick={() => setNavOpen(true)}
            aria-label="Open menu"
            className="rounded-lg border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></svg>
          </button>
          <div className="flex flex-1 flex-wrap items-center justify-end gap-x-3 gap-y-2">
            {/* School-only: manager notification bell, next to the language toggle. */}
            {isSchool && <NotificationBell />}
            <LanguageSwitcher />
            <ThemeToggle />
            <span className="hidden text-slate-400 sm:inline">
              {t("header.signedInAs")} <span className="text-white">{profile.name}</span>
              {slug ? (
                <>
                  {" · "}
                  <span className="text-brand-sage">{slug}</span>
                </>
              ) : null}
            </span>
            <button
              onClick={handleLogout}
              className="rounded-lg border border-ink-700 px-3 py-1.5 text-slate-300 transition-colors hover:border-brand hover:text-white"
            >
              {t("header.signOut")}
            </button>
          </div>
        </header>
        <main className="flex-1 p-4 md:p-6">
          <ModuleProvider module={module}>{children}</ModuleProvider>
        </main>
      </div>
    </div>
  );
}

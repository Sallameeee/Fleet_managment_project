"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  fetchSuperAdminMe,
  logout,
  canSuper,
  cachedSuperAdmin,
  getToken,
  onSessionState,
  SessionError,
  type SuperAdmin,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import Sidebar, { type NavItem } from "@/components/Sidebar";
import ThemeToggle from "@/components/ThemeToggle";
import LanguageSwitcher from "@/components/LanguageSwitcher";
import ReauthModal, { SessionStrip } from "@/components/ReauthModal";

// Each nav item: href, label translation key, the platform permission needed.
const ADMIN_NAV: { href: string; key: string; perm: string }[] = [
  { href: "/dashboard/organizations", key: "nav.organizations", perm: "manage_orgs" },
  { href: "/dashboard/finance", key: "nav.finance", perm: "view_finance" },
  { href: "/dashboard/vehicles", key: "nav.vehicles", perm: "manage_orgs" },
  { href: "/dashboard/drivers", key: "nav.drivers", perm: "manage_orgs" },
  { href: "/dashboard/users", key: "nav.users", perm: "manage_platform_users" },
];

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { t } = useT();
  const [admin, setAdmin] = useState<SuperAdmin | null>(null);
  // Phones: the nav becomes an off-canvas drawer opened by a hamburger. At md+
  // the Sidebar renders exactly as before (static, always visible).
  const [navOpen, setNavOpen] = useState(false);

  // Server-verified guard for the whole /dashboard area. It never signs out by
  // itself: offline / 5xx keep the (cached) panel and retry, a session the
  // server no longer knows asks for the password, only "not a super admin
  // (any more)" signs out.
  const [conn, setConn] = useState<"ok" | "offline">("ok");
  const [reauth, setReauth] = useState(false);
  const [reauthOpen, setReauthOpen] = useState(false);
  const reload = useRef<() => void>(() => {});

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login"); // never signed in in this browser
      return;
    }
    let active = true;
    let needsReload = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let delay = 2000;
    const cached = cachedSuperAdmin();
    if (cached) setAdmin(cached);
    const signOut = () => {
      logout();
      router.replace("/login");
    };
    const askPassword = () => {
      needsReload = true;
      setReauth(true);
      setReauthOpen(true);
    };
    const load = () => {
      if (timer) clearTimeout(timer);
      fetchSuperAdminMe()
        .then((me) => {
          if (!active) return;
          setAdmin(me);
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
      if (kind !== "admin" || !active) return;
      if (state === "reauth") askPassword();
      else if (state === "forbidden") signOut();
      else if (state === "ok") {
        setReauth(false);
        setReauthOpen(false);
        if (needsReload) load();
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

  if (!admin) {
    if (reauth)
      return (
        <div className="min-h-screen">
          <ReauthModal kind="admin" open onDone={() => reload.current()} onLater={() => reload.current()} />
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

  function handleLogout() {
    logout();
    router.replace("/login");
  }

  // Before migration 006 runs, super admins have no permissions object — fall
  // back to showing everything (legacy full access). After migration, the
  // backfilled `view_all` keeps existing admins full; limited staff get gated.
  const hasPerms = !!admin.permissions && Object.keys(admin.permissions).length > 0;
  const items: NavItem[] = ADMIN_NAV.filter(
    (n) => !hasPerms || canSuper(admin, n.perm),
  ).map((n) => ({ href: n.href, label: t(n.key) }));

  return (
    <div className="flex min-h-screen">
      <Sidebar title={t("login.adminTitle")} items={items} open={navOpen} onClose={() => setNavOpen(false)} />
      {/* min-w-0 lets wide tables scroll inside the page instead of stretching it */}
      <div className="flex min-w-0 flex-1 flex-col">
        {conn === "offline" && <SessionStrip state="offline" onRetry={() => reload.current()} />}
        {reauth && !reauthOpen && <SessionStrip state="reauth" onSignIn={() => setReauthOpen(true)} />}
        <ReauthModal
          kind="admin"
          open={reauth && reauthOpen}
          identity={admin.email}
          expectedId={admin.id}
          onDone={() => reload.current()}
          onLater={() => setReauthOpen(false)}
        />
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
            <LanguageSwitcher />
            <ThemeToggle />
            <span className="hidden text-slate-400 sm:inline">
              {t("header.signedInAs")} <span className="text-white">{admin.name}</span>
            </span>
            <button
              onClick={handleLogout}
              className="rounded-lg border border-ink-700 px-3 py-1.5 text-slate-300 transition-colors hover:border-brand hover:text-white"
            >
              {t("header.signOut")}
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  managerMe,
  managerLogout,
  canAccess,
  getManagerSlug,
  getImpersonation,
  getUnreadAlertCount,
  type ManagerProfile,
  type Impersonation,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Sidebar, { type NavItem } from "@/components/Sidebar";
import ThemeToggle from "@/components/ThemeToggle";
import LanguageSwitcher from "@/components/LanguageSwitcher";

// Each nav item: href, translation key for the label, required permission.
const MANAGER_NAV: { href: string; key: string; perm?: string }[] = [
  { href: "/manager", key: "nav.dashboard" },
  { href: "/manager/full-view", key: "nav.fullView", perm: "view_tracking" },
  { href: "/manager/drivers", key: "nav.drivers", perm: "manage_drivers" },
  { href: "/manager/vehicles", key: "nav.vehicles", perm: "manage_vehicles" },
  { href: "/manager/routes", key: "nav.routes", perm: "manage_routes" },
  { href: "/manager/assignments", key: "nav.assignments", perm: "manage_trips" },
  { href: "/manager/trips", key: "nav.trips", perm: "manage_trips" },
  { href: "/manager/alerts", key: "nav.alerts", perm: "manage_trips" },
  { href: "/manager/reports", key: "nav.reports", perm: "view_reports" },
  { href: "/manager/settings", key: "nav.settings", perm: "manage_settings" },
];

export default function ManagerLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { t } = useT();
  const [profile, setProfile] = useState<ManagerProfile | null>(null);
  const [impersonation, setImpersonation] = useState<Impersonation | null>(null);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    setImpersonation(getImpersonation());
  }, []);

  useEffect(() => {
    let active = true;
    managerMe()
      .then((p) => {
        if (active) setProfile(p);
      })
      .catch(() => {
        managerLogout();
        router.replace("/org-login");
      });
    return () => {
      active = false;
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

  if (!profile) return null;

  const items: NavItem[] = MANAGER_NAV.filter(
    (n) => !n.perm || canAccess(profile, n.perm),
  ).map((n) => ({
    href: n.href,
    label: t(n.key),
    badge: n.href === "/manager/alerts" ? unread : undefined,
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
      <Sidebar title={t("login.managerTitle")} items={items} />
      <div className="flex flex-1 flex-col">
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
        <header className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2 border-b border-ink-800 px-6 py-3 text-sm">
          <LanguageSwitcher />
          <ThemeToggle />
          <span className="text-slate-400">
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
        </header>
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchSuperAdminMe, logout, canSuper, type SuperAdmin } from "@/lib/api";
import { useT } from "@/lib/i18n";
import Sidebar, { type NavItem } from "@/components/Sidebar";
import ThemeToggle from "@/components/ThemeToggle";
import LanguageSwitcher from "@/components/LanguageSwitcher";

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

  useEffect(() => {
    // Server-verified guard for the whole /dashboard area.
    let active = true;
    fetchSuperAdminMe()
      .then((me) => {
        if (active) setAdmin(me);
      })
      .catch(() => {
        logout();
        router.replace("/login");
      });
    return () => {
      active = false;
    };
  }, [router]);

  if (!admin) return null;

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

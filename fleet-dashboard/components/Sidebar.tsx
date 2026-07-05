"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export type NavItem = { href: string; label: string; badge?: number; group?: string };

// Defaults = the super-admin nav, so existing `<Sidebar />` usage is unchanged.
const DEFAULT_ITEMS: NavItem[] = [
  { href: "/dashboard/organizations", label: "Organizations" },
  { href: "/dashboard/finance", label: "Finance" },
  { href: "/dashboard/vehicles", label: "Vehicles" },
];

export default function Sidebar({
  title = "Fleet Admin",
  items = DEFAULT_ITEMS,
}: {
  title?: string;
  items?: NavItem[];
}) {
  const pathname = usePathname();
  // The active item is the LONGEST href that matches, so a root item like
  // "/manager" doesn't stay highlighted on "/manager/drivers".
  const activeHref = items.reduce<string | null>((best, it) => {
    const matches = pathname === it.href || pathname.startsWith(it.href + "/");
    return matches && it.href.length > (best?.length ?? -1) ? it.href : best;
  }, null);

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-ink-800 bg-ink-900/50 p-4">
      <div className="mb-6 flex items-center gap-2 px-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-sm font-bold text-white">
          F
        </div>
        <span className="font-semibold text-white">{title}</span>
      </div>

      <nav className="space-y-1">
        {items.map((item, i) => {
          const active = item.href === activeHref;
          // A group header renders before the first visible item of each group.
          const showGroup = !!item.group && item.group !== items[i - 1]?.group;
          return (
            <div key={item.href}>
              {showGroup && (
                <div className={"px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600 " + (i === 0 ? "" : "pt-4")}>
                  {item.group}
                </div>
              )}
              <Link
                href={item.href}
                className={
                  "flex items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors " +
                  (active
                    ? "bg-brand/15 font-medium text-brand-sage"
                    : "text-slate-300 hover:bg-ink-800 hover:text-white")
                }
              >
                <span>{item.label}</span>
                {item.badge ? (
                  <span className="ml-2 inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-red-500 px-1.5 text-xs font-semibold text-white">
                    {item.badge > 99 ? "99+" : item.badge}
                  </span>
                ) : null}
              </Link>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

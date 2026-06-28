"use client";

import { useT } from "@/lib/i18n";

// Colored status pill: active=green, suspended=amber, expired=red.
const STYLES: Record<string, string> = {
  active: "bg-brand/15 text-brand-sage border-brand/30",
  suspended: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  expired: "bg-red-500/15 text-red-300 border-red-500/30",
};

const LABEL_KEY: Record<string, string> = {
  active: "common.active",
  inactive: "common.inactive",
  suspended: "common.suspended",
  expired: "common.expired",
};

export default function StatusBadge({ status }: { status: string }) {
  const { t } = useT();
  const cls = STYLES[status] ?? "bg-ink-800 text-slate-300 border-ink-700";
  const label = LABEL_KEY[status] ? t(LABEL_KEY[status]) : status;
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

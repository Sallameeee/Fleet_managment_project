"use client";

import { useEffect, useState } from "react";
import { listAllDrivers, type AdminDriver } from "@/lib/api";
import { useT } from "@/lib/i18n";
import StatusBadge from "@/components/StatusBadge";

export default function DriversPage() {
  const { t } = useT();
  const [drivers, setDrivers] = useState<AdminDriver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listAllDrivers()
      .then((d) => {
        if (active) setDrivers(d);
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : t("common.loadFailed"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div>
      <h1 className="mb-1 text-2xl font-semibold text-white">{t("nav.drivers")}</h1>
      <p className="mb-6 text-sm text-slate-400">
        {loading ? t("common.loading") : `${drivers.length}`}
      </p>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.driver")}</th>
              <th className="px-4 py-3">{t("common.username")}</th>
              <th className="px-4 py-3">{t("common.organization")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3">{t("summary.online")}</th>
              <th className="px-4 py-3">{t("drivers.currentVehicle")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && drivers.length === 0 && !error && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-500">
                  {t("common.none")}
                </td>
              </tr>
            )}
            {drivers.map((d) => (
              <tr key={d.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 font-medium text-white">{d.name}</td>
                <td className="px-4 py-3 text-slate-400">{d.username}</td>
                <td className="px-4 py-3 text-slate-200">{d.org_name ?? "—"}</td>
                <td className="px-4 py-3">
                  <StatusBadge status={d.is_active ? "active" : "inactive"} />
                </td>
                <td className="px-4 py-3">
                  {d.online ? (
                    <span className="inline-flex items-center gap-1.5 text-brand-sage">
                      <span className="h-2 w-2 rounded-full bg-brand-sage" />
                      {t("common.online")}
                    </span>
                  ) : (
                    <span className="text-slate-500">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-300">{d.current_vehicle ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

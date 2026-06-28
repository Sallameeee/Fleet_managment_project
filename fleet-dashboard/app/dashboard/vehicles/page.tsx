"use client";

import { useEffect, useState } from "react";
import { listAllVehicles, type AdminVehicle } from "@/lib/api";
import { useT } from "@/lib/i18n";

export default function VehiclesPage() {
  const { t } = useT();
  const [vehicles, setVehicles] = useState<AdminVehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listAllVehicles()
      .then((v) => {
        if (active) setVehicles(v);
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
      <h1 className="mb-1 text-2xl font-semibold text-white">{t("nav.vehicles")}</h1>
      <p className="mb-6 text-sm text-slate-400">
        {loading ? t("common.loading") : `${vehicles.length}`}
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
              <th className="px-4 py-3">{t("common.organization")}</th>
              <th className="px-4 py-3">{t("vehicles.busNumber")}</th>
              <th className="px-4 py-3">{t("vehicles.plate")}</th>
              <th className="px-4 py-3">{t("common.active")}</th>
              <th className="px-4 py-3">{t("summary.online")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && vehicles.length === 0 && !error && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  {t("common.none")}
                </td>
              </tr>
            )}
            {vehicles.map((v) => (
              <tr key={v.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 text-white">{v.org_name ?? "—"}</td>
                <td className="px-4 py-3 font-medium text-slate-200">{v.bus_number}</td>
                <td className="px-4 py-3 text-slate-400">{v.plate_number ?? "—"}</td>
                <td className="px-4 py-3">
                  <span className={v.is_active ? "text-brand-sage" : "text-slate-500"}>
                    {v.is_active ? t("common.active") : t("common.inactive")}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {v.has_active_trip ? (
                    <span className="inline-flex items-center gap-1.5 text-brand-sage">
                      <span className="h-2 w-2 rounded-full bg-brand-sage" />
                      {t("common.online")}
                    </span>
                  ) : (
                    <span className="text-slate-500">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

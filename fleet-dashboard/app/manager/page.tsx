"use client";

import { useEffect, useState } from "react";
import { getDashboardSummary, type DashboardSummary } from "@/lib/manager";
import { useT } from "@/lib/i18n";

function Card({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-5">
      <div className="text-sm text-slate-400">{label}</div>
      <div className={`mt-1 text-3xl font-semibold ${accent ?? "text-white"}`}>{value}</div>
    </div>
  );
}

export default function ManagerDashboardPage() {
  const { t } = useT();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDashboardSummary()
      .then((d) => {
        if (active) setData(d);
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

  if (loading) return <div className="text-slate-500">{t("common.loading")}</div>;
  if (error)
    return (
      <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
        {error}
      </div>
    );
  if (!data) return null;

  const d = data.drivers;
  const top = data.top_driver;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-white">{t("nav.dashboard")}</h1>

      {/* Driver count cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card label={t("summary.totalDrivers")} value={d.total} />
        <Card label={t("summary.online")} value={d.online} accent="text-brand-sage" />
        <Card label={t("summary.offline")} value={d.offline} accent="text-slate-300" />
        <Card label={t("summary.workingNow")} value={d.working_now} accent="text-brand" />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Top driver this month */}
        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-5">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {t("summary.topDriver")}
          </h2>
          {top ? (
            <div>
              <div className="text-lg font-semibold text-white">{top.name ?? "—"}</div>
              <dl className="mt-3 space-y-1.5 text-sm">
                <Row k={t("summary.actualKm")} v={top.actual_km.toFixed(2)} />
                <Row k={t("summary.trips")} v={String(top.trips)} />
                <Row k={t("summary.score")} v={top.score === null ? "—" : String(top.score)} />
              </dl>
            </div>
          ) : (
            <div className="text-sm text-slate-500">{t("summary.noTripsMonth")}</div>
          )}
        </div>

        {/* Live alerts feed */}
        <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-5 lg:col-span-2">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
            {t("summary.liveAlerts")}
          </h2>
          {data.alerts.length === 0 ? (
            <div className="text-sm text-slate-500">{t("alerts.noAlerts")}</div>
          ) : (
            <ul className="divide-y divide-ink-800">
              {data.alerts.map((a) => (
                <li key={a.id} className="flex items-start justify-between gap-3 py-2.5 text-sm">
                  <div>
                    <span className="mr-2 inline-flex rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs capitalize text-amber-300">
                      {a.type.replace("_", " ")}
                    </span>
                    <span className="text-slate-300">{a.detail}</span>
                    {a.driver_name && <span className="text-slate-500"> · {a.driver_name}</span>}
                  </div>
                  <span className="shrink-0 text-xs text-slate-500">
                    {a.occurred_at ? a.occurred_at.replace("T", " ").slice(0, 16) : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-slate-400">{k}</dt>
      <dd className="text-white">{v}</dd>
    </div>
  );
}

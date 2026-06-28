"use client";

import { useEffect, useState } from "react";
import { getFinance, type FinanceSummary } from "@/lib/api";
import { useT } from "@/lib/i18n";
import StatusBadge from "@/components/StatusBadge";

function money(n: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Card({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-5">
      <div className="text-sm text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accent ?? "text-white"}`}>{money(value)}</div>
    </div>
  );
}

export default function FinancePage() {
  const { t } = useT();
  const [data, setData] = useState<FinanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getFinance()
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

  return (
    <div>
      <h1 className="mb-1 text-2xl font-semibold text-white">{t("nav.finance")}</h1>
      <p className="mb-6 text-sm text-slate-400">{t("finance.subtitle")}</p>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}
      {loading && <div className="text-slate-500">{t("common.loading")}</div>}

      {data && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Card label={t("finance.totalExpected")} value={data.totals.expected} />
            <Card label={t("finance.totalCollected")} value={data.totals.collected} accent="text-brand-sage" />
            <Card
              label={t("finance.totalOutstanding")}
              value={data.totals.outstanding}
              accent={data.totals.outstanding > 0 ? "text-amber-300" : "text-white"}
            />
          </div>

          <div className="overflow-hidden rounded-xl border border-ink-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-4 py-3">{t("common.organization")}</th>
                  <th className="px-4 py-3">{t("common.status")}</th>
                  <th className="px-4 py-3">{t("orgs.monthlyFee")}</th>
                  <th className="px-4 py-3">{t("finance.expected")}</th>
                  <th className="px-4 py-3">{t("finance.collected")}</th>
                  <th className="px-4 py-3">{t("finance.outstanding")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {data.organizations.map((r) => (
                  <tr key={r.id} className="hover:bg-ink-900/40">
                    <td className="px-4 py-3 font-medium text-white">{r.name}</td>
                    <td className="px-4 py-3">
                      <StatusBadge status={r.status} />
                    </td>
                    <td className="px-4 py-3 text-slate-300">{money(r.monthly_fee)}</td>
                    <td className="px-4 py-3 text-slate-300">{money(r.expected)}</td>
                    <td className="px-4 py-3 text-brand-sage">{money(r.collected)}</td>
                    <td
                      className={`px-4 py-3 font-medium ${
                        r.outstanding > 0 ? "text-amber-300" : "text-slate-400"
                      }`}
                    >
                      {money(r.outstanding)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

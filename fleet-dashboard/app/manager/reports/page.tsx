"use client";

import { useState } from "react";
import { getReport, downloadReportPdf, type ReportParams, type ReportResponse } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";

const TYPES = ["drivers", "trips", "kilometers", "speed"] as const;

function km(n: number | null): string {
  return n === null || n === undefined ? "—" : n.toFixed(2);
}

export default function ManagerReportsPage() {
  const { t } = useT();
  const [selected, setSelected] = useState<Record<string, boolean>>({ drivers: true, kilometers: true });
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function buildParams(): ReportParams | null {
    const types = TYPES.filter((ty) => selected[ty]);
    if (types.length === 0) {
      setError(t("reports.pickType"));
      return null;
    }
    if (period === "custom" && (!dateFrom || !dateTo)) {
      setError(t("reports.customNeedsDates"));
      return null;
    }
    return period === "custom"
      ? { types, date_from: dateFrom, date_to: dateTo }
      : { types, period };
  }

  async function generate() {
    const params = buildParams();
    if (!params) return;
    setLoading(true);
    setError(null);
    try {
      setReport(await getReport(params));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setLoading(false);
    }
  }

  async function downloadPdf() {
    const params = buildParams();
    if (!params) return;
    setDownloading(true);
    setError(null);
    try {
      await downloadReportPdf(params);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.reports")}</h1>
        <p className="text-sm text-slate-400">{t("reports.subtitle")}</p>
      </div>

      {/* Controls */}
      <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-5">
        <div className="mb-4 flex flex-wrap gap-4">
          {TYPES.map((ty) => (
            <label key={ty} className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={!!selected[ty]} onChange={(e) => setSelected((s) => ({ ...s, [ty]: e.target.checked }))} className="h-4 w-4 accent-[#3AA76D]" />
              {ty === "drivers" ? t("nav.drivers") : ty === "trips" ? t("nav.trips") : ty === "kilometers" ? t("reports.kmShort") : t("reports.speed")}
            </label>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("reports.period")}</span>
            <select value={period} onChange={(e) => setPeriod(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none">
              <option value="today">{t("reports.today")}</option>
              <option value="week">{t("reports.week")}</option>
              <option value="month">{t("reports.month")}</option>
              <option value="custom">{t("reports.custom")}</option>
            </select>
          </label>
          {period === "custom" && (
            <>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("common.from")}</span>
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100" />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("common.to")}</span>
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100" />
              </label>
            </>
          )}
          <button onClick={generate} disabled={loading} className="rounded-lg bg-brand px-5 py-2 text-sm font-medium text-white hover:bg-brand-sage disabled:opacity-60">
            {loading ? t("common.loading") : t("reports.generate")}
          </button>
          <button onClick={downloadPdf} disabled={downloading} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white disabled:opacity-60">
            {downloading ? t("common.loading") : t("reports.downloadPdf")}
          </button>
        </div>
        {error && <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
      </div>

      {/* Rendered sections */}
      {report && (
        <div className="space-y-6">
          <p className="text-sm text-slate-400">
            {report.org.name} · {report.period.from} → {report.period.to} ({report.period.preset})
          </p>

          {report.sections.drivers && (
            <Section title={t("nav.drivers")}>
              <Table head={[t("common.driver"), t("nav.trips"), t("reports.plannedKm"), t("reports.actualKm"), t("reports.diff")]}>
                {report.sections.drivers.map((r) => (
                  <tr key={r.driver_id}>
                    <Td>{r.driver_name}</Td><Td>{r.trips}</Td><Td>{km(r.planned_km)}</Td><Td>{km(r.actual_km)}</Td>
                    <Td accent={r.difference_km < 0 ? "neg" : "pos"}>{km(r.difference_km)}</Td>
                  </tr>
                ))}
              </Table>
            </Section>
          )}

          {report.sections.kilometers && (
            <Section title={t("reports.kilometers")}>
              <Table head={[t("common.vehicle"), t("reports.plannedKm"), t("reports.actualKm"), t("reports.diff")]}>
                {report.sections.kilometers.by_vehicle.map((r, i) => (
                  <tr key={`v${i}`}><Td>{r.vehicle_bus_number}</Td><Td>{km(r.planned_km)}</Td><Td>{km(r.actual_km)}</Td><Td accent={r.difference_km < 0 ? "neg" : "pos"}>{km(r.difference_km)}</Td></tr>
                ))}
              </Table>
              <div className="h-3" />
              <Table head={[t("common.driver"), t("reports.plannedKm"), t("reports.actualKm"), t("reports.diff")]}>
                {report.sections.kilometers.by_driver.map((r, i) => (
                  <tr key={`d${i}`}><Td>{r.driver_name}</Td><Td>{km(r.planned_km)}</Td><Td>{km(r.actual_km)}</Td><Td accent={r.difference_km < 0 ? "neg" : "pos"}>{km(r.difference_km)}</Td></tr>
                ))}
              </Table>
            </Section>
          )}

          {report.sections.trips && (
            <Section title={t("nav.trips")}>
              <Table head={[t("common.driver"), t("common.route"), t("reports.bus"), t("common.status"), t("reports.plannedKm"), t("reports.actualKm"), t("reports.diff")]}>
                {report.sections.trips.map((r) => (
                  <tr key={r.trip_id}>
                    <Td>{r.driver_name}</Td><Td>{r.route_name}</Td><Td>{r.vehicle_bus_number}</Td><Td>{r.status}</Td>
                    <Td>{km(r.planned_km)}</Td><Td>{km(r.actual_km)}</Td><Td accent={r.difference_km < 0 ? "neg" : "pos"}>{km(r.difference_km)}</Td>
                  </tr>
                ))}
              </Table>
            </Section>
          )}

          {report.sections.speed && (
            <Section title={t("reports.speed")}>
              <Table head={[t("common.driver"), t("reports.maxKmh"), t("reports.avgKmh"), t("reports.speedingAlerts")]}>
                {report.sections.speed.map((r, i) => (
                  <tr key={`s${i}`}><Td>{r.driver_name}</Td><Td>{r.max_speed ?? "—"}</Td><Td>{r.avg_speed ?? "—"}</Td><Td>{r.speeding_alerts}</Td></tr>
                ))}
              </Table>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 className="mb-2 text-lg font-semibold text-white">{title}</h2>
      {children}
    </div>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-ink-800">
      <table className="w-full text-left text-sm">
        <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
          <tr>{head.map((h) => <th key={h} className="px-4 py-2.5">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-ink-800">{children}</tbody>
      </table>
    </div>
  );
}

function Td({ children, accent }: { children: React.ReactNode; accent?: "pos" | "neg" }) {
  const cls = accent === "neg" ? "text-amber-300" : accent === "pos" ? "text-brand-sage" : "text-slate-300";
  return <td className={`px-4 py-2.5 ${cls}`}>{children}</td>;
}

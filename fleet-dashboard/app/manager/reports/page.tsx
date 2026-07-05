"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getReport,
  downloadReportPdf,
  listVehicles,
  listDrivers,
  listReportSchedules,
  createReportSchedule,
  deleteReportSchedule,
  type ReportParams,
  type ReportResponse,
  type ManagerVehicle,
  type ManagerDriver,
  type ReportSchedule,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";

const TYPES = ["drivers", "trips", "kilometers", "speed"] as const;
const FREQS = ["daily", "weekly", "monthly"] as const;

function km(n: number | null): string {
  return n === null || n === undefined ? "—" : n.toFixed(2);
}

export default function ManagerReportsPage() {
  const { t } = useT();

  // Step A — subject
  const [subjectKind, setSubjectKind] = useState<"vehicle" | "driver">("vehicle");
  const [subjectId, setSubjectId] = useState(""); // "" = all
  const [filter, setFilter] = useState("");
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);

  // Step B — types
  const [selected, setSelected] = useState<Record<string, boolean>>({ drivers: true, kilometers: true });
  // Step C — period
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Schedules
  const [schedules, setSchedules] = useState<ReportSchedule[]>([]);
  const [freq, setFreq] = useState<(typeof FREQS)[number]>("weekly");
  const [email, setEmail] = useState("");
  const [savingSched, setSavingSched] = useState(false);
  const [schedError, setSchedError] = useState<string | null>(null);

  useEffect(() => {
    listVehicles().then(setVehicles).catch(() => {});
    listDrivers().then(setDrivers).catch(() => {});
    reloadSchedules();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const reloadSchedules = useCallback(async () => {
    try {
      setSchedules(await listReportSchedules());
    } catch {
      /* ignore */
    }
  }, []);

  const items =
    subjectKind === "vehicle"
      ? vehicles.map((v) => ({ id: v.id, label: v.bus_number }))
      : drivers.map((d) => ({ id: d.id, label: d.name }));
  const shown = items.filter((o) => o.label.toLowerCase().includes(filter.trim().toLowerCase()));
  const subjectLabel =
    subjectId === "" ? t("reports.allSubjects") : items.find((o) => o.id === subjectId)?.label ?? "—";

  function setKind(kind: "vehicle" | "driver") {
    setSubjectKind(kind);
    setSubjectId("");
    setFilter("");
  }

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
    const base: ReportParams =
      period === "custom" ? { types, date_from: dateFrom, date_to: dateTo } : { types, period };
    if (subjectId) {
      if (subjectKind === "vehicle") base.vehicle_id = subjectId;
      else base.driver_id = subjectId;
    }
    return base;
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

  async function addSchedule() {
    const types = TYPES.filter((ty) => selected[ty]);
    if (types.length === 0) {
      setSchedError(t("reports.pickType"));
      return;
    }
    if (!email.trim()) {
      setSchedError(t("reports.emailAddress"));
      return;
    }
    setSavingSched(true);
    setSchedError(null);
    try {
      await createReportSchedule({
        frequency: freq,
        subject_kind: subjectId ? subjectKind : "all",
        subject_id: subjectId || null,
        types,
        period: period === "custom" ? "week" : period,
        email: email.trim(),
      });
      setEmail("");
      await reloadSchedules();
    } catch (e) {
      setSchedError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setSavingSched(false);
    }
  }

  async function removeSchedule(id: string) {
    try {
      await deleteReportSchedule(id);
      await reloadSchedules();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  const typeLabel = (ty: string) =>
    ty === "drivers" ? t("nav.drivers") : ty === "trips" ? t("nav.trips") : ty === "kilometers" ? t("reports.kmShort") : t("reports.speed");
  const freqLabel = (f: string) => (f === "daily" ? t("reports.daily") : f === "weekly" ? t("reports.weekly") : t("reports.monthly"));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.reports")}</h1>
        <p className="text-sm text-slate-400">{t("reports.subtitle")}</p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(320px,380px)_1fr]">
        {/* ---- Selection panel ---- */}
        <div className="space-y-4">
          {/* Step A — subject */}
          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand-sage">{t("reports.stepSubject")}</h3>
            <div className="mb-3 inline-flex rounded-lg border border-ink-700 p-0.5">
              {(["vehicle", "driver"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setKind(k)}
                  className={"rounded-md px-4 py-1.5 text-sm font-medium transition-colors " + (subjectKind === k ? "bg-brand text-white" : "text-slate-400 hover:text-white")}
                >
                  {k === "vehicle" ? t("common.vehicle") : t("common.driver")}
                </button>
              ))}
            </div>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("reports.searchSubject")}
              className="mb-2 w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none"
            />
            <div className="max-h-52 space-y-1 overflow-y-auto pr-1">
              <button
                onClick={() => setSubjectId("")}
                className={"block w-full rounded-md px-3 py-1.5 text-start text-sm transition-colors " + (subjectId === "" ? "bg-brand/15 text-brand-sage" : "text-slate-300 hover:bg-ink-800")}
              >
                {t("reports.allSubjects")} {subjectKind === "vehicle" ? t("common.vehicle") : t("common.driver")}
              </button>
              {shown.map((o) => (
                <button
                  key={o.id}
                  onClick={() => setSubjectId(o.id)}
                  className={"block w-full truncate rounded-md px-3 py-1.5 text-start text-sm transition-colors " + (subjectId === o.id ? "bg-brand/15 text-brand-sage" : "text-slate-300 hover:bg-ink-800")}
                >
                  {o.label}
                </button>
              ))}
              {shown.length === 0 && <p className="px-3 py-2 text-xs text-slate-600">{t("reports.searchSubject")}</p>}
            </div>
          </div>

          {/* Step B — types */}
          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand-sage">{t("reports.stepType")}</h3>
            <div className="grid grid-cols-2 gap-2">
              {TYPES.map((ty) => (
                <label key={ty} className={"flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors " + (selected[ty] ? "border-brand bg-brand/10 text-white" : "border-ink-700 text-slate-300 hover:border-ink-600")}>
                  <input type="checkbox" checked={!!selected[ty]} onChange={(e) => setSelected((s) => ({ ...s, [ty]: e.target.checked }))} className="h-4 w-4 accent-[#3AA76D]" />
                  {typeLabel(ty)}
                </label>
              ))}
            </div>
          </div>

          {/* Step C — period */}
          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand-sage">{t("reports.stepPeriod")}</h3>
            <div className="flex flex-wrap gap-1.5">
              {(["today", "week", "month", "custom"] as const).map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriod(p)}
                  className={"rounded-lg border px-3 py-1.5 text-sm transition-colors " + (period === p ? "border-brand bg-brand/10 text-white" : "border-ink-700 text-slate-300 hover:border-ink-600")}
                >
                  {p === "today" ? t("reports.today") : p === "week" ? t("reports.week") : p === "month" ? t("reports.month") : t("reports.custom")}
                </button>
              ))}
            </div>
            {period === "custom" && (
              <div className="mt-3 grid grid-cols-2 gap-2">
                <label className="block">
                  <span className="mb-1 block text-xs text-slate-400">{t("common.from")}</span>
                  <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100" />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs text-slate-400">{t("common.to")}</span>
                  <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100" />
                </label>
              </div>
            )}
          </div>

          {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}

          <div className="flex gap-2">
            <button onClick={generate} disabled={loading} className="flex-1 rounded-lg bg-brand px-5 py-2.5 text-sm font-medium text-white hover:bg-brand-sage disabled:opacity-60">
              {loading ? t("common.loading") : t("reports.generate")}
            </button>
            <button onClick={downloadPdf} disabled={downloading} className="rounded-lg border border-ink-700 px-4 py-2.5 text-sm text-slate-300 hover:border-brand hover:text-white disabled:opacity-60">
              {downloading ? t("common.loading") : t("reports.downloadPdf")}
            </button>
          </div>

          {/* Schedule to email */}
          <div className="rounded-xl border border-ink-800 bg-ink-900/50 p-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-brand-sage">{t("reports.scheduleTitle")}</h3>
              <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300">{t("reports.deliverySoon")}</span>
            </div>
            <p className="mt-1 text-xs text-slate-500">{t("reports.scheduleHint")}</p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className="block">
                <span className="mb-1 block text-xs text-slate-400">{t("reports.frequency")}</span>
                <select value={freq} onChange={(e) => setFreq(e.target.value as (typeof FREQS)[number])} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none">
                  {FREQS.map((f) => <option key={f} value={f}>{freqLabel(f)}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-slate-400">{t("reports.emailAddress")}</span>
                <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ops@example.com" className="w-full rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none" />
              </label>
            </div>
            {schedError && <p className="mt-2 text-xs text-red-300">{schedError}</p>}
            <button onClick={addSchedule} disabled={savingSched} className="mt-3 w-full rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-200 hover:border-brand hover:text-white disabled:opacity-60">
              {savingSched ? t("common.loading") : `+ ${t("reports.addSchedule")}`}
            </button>

            {schedules.length > 0 && (
              <ul className="mt-3 space-y-1.5">
                {schedules.map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-2 rounded-lg border border-ink-800 px-3 py-2 text-xs">
                    <div className="min-w-0">
                      <div className="font-medium text-white">{freqLabel(s.frequency)} · {s.email}</div>
                      <div className="truncate text-slate-500">{s.types.split(",").map(typeLabel).join(", ")} · {s.period}</div>
                    </div>
                    <button onClick={() => removeSchedule(s.id)} className="shrink-0 rounded-md border border-red-500/40 px-2 py-1 text-red-300 hover:bg-red-500/10" title={t("common.delete")}>✕</button>
                  </li>
                ))}
              </ul>
            )}
            {schedules.length === 0 && <p className="mt-3 text-xs text-slate-600">{t("reports.noSchedules")}</p>}
          </div>
        </div>

        {/* ---- Result panel ---- */}
        <div className="min-w-0">
          {!report ? (
            <div className="flex h-64 items-center justify-center rounded-xl border border-dashed border-ink-700 bg-ink-900/30 px-6 text-center text-sm text-slate-500">
              {t("reports.emptyResult")}
            </div>
          ) : (
            <div className="space-y-5 rounded-xl border border-ink-800 bg-ink-900/40 p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-ink-800 pb-3">
                <h2 className="text-lg font-semibold text-white">{report.org.name}</h2>
                <p className="text-sm text-slate-400">
                  {report.period.from} → {report.period.to}
                  <span className="ms-2 rounded-full bg-ink-800 px-2 py-0.5 text-xs text-slate-300">
                    {t("reports.subjectLabel")}: {subjectLabel}
                  </span>
                </p>
              </div>

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
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-brand-sage">{title}</h3>
      {children}
    </div>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border border-ink-800">
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

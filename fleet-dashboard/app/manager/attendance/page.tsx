"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getAttendanceColumns,
  exportAttendance,
  listRoutes,
  listPassengers,
  type AttendanceColumn,
  type ManagerRoute,
  type ManagerPassenger,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";

type Scope = "all" | "student";
type Fmt = "xlsx" | "pdf";

// Last day of a YYYY-MM month.
function monthRange(month: string): { from: string; to: string } | null {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const last = new Date(y, mo, 0).getDate();
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, "0")}` };
}

export default function ManagerAttendancePage() {
  const { t } = useT();
  const toast = useToast();

  const [columns, setColumns] = useState<AttendanceColumn[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [students, setStudents] = useState<ManagerPassenger[]>([]);

  const [scope, setScope] = useState<Scope>("all");
  const [routeId, setRouteId] = useState("");
  const [studentId, setStudentId] = useState("");
  const [month, setMonth] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [format, setFormat] = useState<Fmt>("xlsx");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const c = await getAttendanceColumns();
      setColumns(c.columns);
      setSelected(new Set(c.default));
    } catch {
      /* columns stay empty */
    }
    listRoutes().then(setRoutes).catch(() => {});
    listPassengers().then(setStudents).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function doExport() {
    // Keep the manager's chosen column ORDER as defined by the columns list.
    const cols = columns.map((c) => c.key).filter((k) => selected.has(k));
    if (cols.length === 0) {
      toast.error(t("att.columns"));
      return;
    }
    let dateFrom = from || undefined;
    let dateTo = to || undefined;
    if (scope === "student") {
      if (!studentId) {
        toast.error(t("att.pickStudent"));
        return;
      }
      const r = month ? monthRange(month) : null;
      if (r) {
        dateFrom = r.from;
        dateTo = r.to;
      }
    }
    setBusy(true);
    try {
      await exportAttendance({
        format,
        columns: cols,
        route_id: scope === "all" ? routeId || undefined : undefined,
        student_id: scope === "student" ? studentId : undefined,
        date_from: dateFrom,
        date_to: dateTo,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setBusy(false);
    }
  }

  const box = "rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none";

  return (
    <div className="max-w-3xl">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white">{t("att.title")}</h1>
        <p className="text-sm text-slate-400">{t("att.subtitle")}</p>
      </div>

      <div className="space-y-5 rounded-xl border border-ink-800 bg-ink-900/40 p-5">
        {/* Scope */}
        <div className="inline-flex rounded-lg border border-ink-700 p-0.5">
          {(["all", "student"] as Scope[]).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={"rounded-md px-3 py-1.5 text-sm font-medium transition-colors " + (scope === s ? "bg-brand text-white" : "text-slate-400 hover:text-white")}
            >
              {s === "all" ? t("att.scopeAll") : t("att.scopeStudent")}
            </button>
          ))}
        </div>

        {/* Filters */}
        {scope === "all" ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="block">
              <span className="mb-1.5 block text-sm text-slate-300">{t("att.route")}</span>
              <select value={routeId} onChange={(e) => setRouteId(e.target.value)} className={box + " w-full"}>
                <option value="">{t("att.allRoutes")}</option>
                {routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm text-slate-300">{t("att.from")}</span>
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={box + " w-full"} />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm text-slate-300">{t("att.to")}</span>
              <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={box + " w-full"} />
            </label>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-sm text-slate-300">{t("att.student")}</span>
              <select value={studentId} onChange={(e) => setStudentId(e.target.value)} className={box + " w-full"}>
                <option value="">{t("att.pickStudent")}</option>
                {students.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm text-slate-300">{t("att.month")}</span>
              <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className={box + " w-full"} />
            </label>
          </div>
        )}

        {/* Column picker */}
        <div>
          <span className="mb-2 block text-sm text-slate-300">{t("att.columns")}</span>
          <div className="flex flex-wrap gap-2">
            {columns.map((c) => {
              const on = selected.has(c.key);
              return (
                <button
                  key={c.key}
                  onClick={() => toggle(c.key)}
                  className={"rounded-full border px-3 py-1.5 text-sm transition-colors " + (on ? "border-brand bg-brand/15 text-brand-sage" : "border-ink-700 text-slate-400 hover:text-white")}
                >
                  {on ? "✓ " : ""}{c.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Format + export */}
        <div className="flex flex-wrap items-end justify-between gap-3">
          <label className="block">
            <span className="mb-1.5 block text-sm text-slate-300">{t("att.format")}</span>
            <select value={format} onChange={(e) => setFormat(e.target.value as Fmt)} className={box}>
              <option value="xlsx">Excel (.xlsx)</option>
              <option value="pdf">PDF</option>
            </select>
          </label>
          <Button onClick={doExport} loading={busy} className="w-auto px-6">{t("att.export")}</Button>
        </div>
      </div>
    </div>
  );
}

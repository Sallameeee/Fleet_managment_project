"use client";

import { useCallback, useEffect, useState } from "react";
import { listParentReports, resolveParentReport, type ManagerReport } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import { useFocusHighlight } from "@/lib/useFocusHighlight";

type Filter = "open" | "resolved" | "all";
const FILTERS: Filter[] = ["open", "resolved", "all"];

export default function ManagerParentReportsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();

  const [filter, setFilter] = useState<Filter>("open");
  const [rows, setRows] = useState<ManagerReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listParentReports(filter === "all" ? undefined : filter));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  useEffect(() => {
    if (isSchool) load();
  }, [isSchool, load]);

  const { focus, highlight } = useFocusHighlight("rep-", !loading);
  useEffect(() => {
    if (focus) setFilter("all");
  }, [focus]);

  async function resolve(r: ManagerReport) {
    setActioningId(r.id);
    try {
      await resolveParentReport(r.id);
      toast.success(t("prep.resolvedToast"));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setActioningId(null);
    }
  }

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("prep.title")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("prep.subtitle")}</p>
        </div>
        <button onClick={load} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white">
          ↻ {t("common.reload")}
        </button>
      </div>

      <div className="mb-5 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={
              "rounded-full px-4 py-1.5 text-sm font-medium transition-colors " +
              (filter === f ? "bg-brand text-white" : "border border-ink-700 text-slate-300 hover:border-brand hover:text-white")
            }
          >
            {f === "all" ? t("prep.all") : f === "open" ? t("prep.open") : t("prep.resolved")}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      {!loading && rows.length === 0 && !error && (
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("prep.none")}</div>
      )}

      <div className="space-y-4">
        {rows.map((r) => {
          const open = r.status === "open";
          return (
            <div
              key={r.id}
              id={"rep-" + r.id}
              className={"rounded-xl border border-ink-800 bg-ink-900/40 p-5 " + (highlight === r.id ? "ring-2 ring-brand ring-offset-2 ring-offset-ink-950" : "")}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  {/* Complainant — prominent, with contact info beneath. */}
                  <h2 className="text-lg font-semibold text-white">{r.parent_name ?? "—"}</h2>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm">
                    {r.parent_phone ? (
                      <a href={`tel:${r.parent_phone}`} className="font-medium text-brand-sage hover:underline">📞 {r.parent_phone}</a>
                    ) : (
                      <span className="text-slate-600">📞 {t("prep.noPhone")}</span>
                    )}
                    {r.parent_email && (
                      <a href={`mailto:${r.parent_email}`} className="truncate text-slate-400 hover:text-brand-sage">✉ {r.parent_email}</a>
                    )}
                  </div>
                  {/* About a specific child → child name + the route they ride. */}
                  {r.student_name && (
                    <div className="mt-1.5 inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border border-ink-700 bg-ink-850 px-2.5 py-1 text-xs text-slate-300">
                      <span>{t("prep.about")} <span className="font-semibold text-white">{r.student_name}</span></span>
                      <span className="text-slate-600">·</span>
                      <span>🚌 {t("prep.route")}: <span className="text-slate-200">{r.student_route_name ?? t("parents.noRoute")}</span></span>
                    </div>
                  )}
                </div>
                <span
                  className={
                    "rounded-full border px-3 py-1 text-xs font-semibold " +
                    (open ? "border-amber-500/40 bg-amber-500/10 text-amber-300" : "border-emerald-500/40 bg-emerald-500/10 text-emerald-300")
                  }
                >
                  {open ? t("prep.open") : t("prep.resolved")}
                </span>
              </div>

              {/* Subject + message. */}
              <div className="mt-3 text-sm font-semibold text-white">{r.subject}</div>
              <p className="mt-1 whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-sm text-slate-200">{r.message}</p>

              {open && (
                <div className="mt-4 flex justify-end">
                  <button
                    onClick={() => resolve(r)}
                    disabled={actioningId === r.id}
                    className="rounded-lg bg-brand px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage disabled:opacity-50"
                  >
                    {t("prep.resolve")}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

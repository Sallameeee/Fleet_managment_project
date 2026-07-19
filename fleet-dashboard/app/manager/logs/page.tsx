"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getTodayLogs, getAllLogs, type LogEvent } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import EventList from "@/components/EventList";

const POLL_MS = 15000; // live feed refresh cadence

type Mode = "today" | "all";

export default function ManagerLogsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const [mode, setMode] = useState<Mode>("today");
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;

  const load = useCallback(async (m: Mode, spinner: boolean) => {
    if (spinner) setLoading(true);
    setError(null);
    try {
      const data = m === "today" ? await getTodayLogs() : await getAllLogs();
      // Ignore a response that arrived after the user switched modes.
      if (modeRef.current === m) setEvents(data);
    } catch (e) {
      if (modeRef.current === m) setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      if (spinner) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isSchool) return;
    load(mode, true);
    // Live auto-append only in "today" mode (polling — no push).
    if (mode !== "today") return;
    const id = setInterval(() => load("today", false), POLL_MS);
    return () => clearInterval(id);
  }, [isSchool, mode, load]);

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("logs.title")}</h1>
          <p className="text-sm text-slate-400">
            {loading ? t("common.loading") : mode === "today" ? t("logs.subtitleToday") : t("logs.subtitleAll")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {mode === "today" && !loading && (
            <span className="inline-flex items-center gap-1.5 text-xs text-emerald-400">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              {t("logs.live")}
            </span>
          )}
          <button onClick={() => load(mode, true)} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white">
            ↻ {t("common.reload")}
          </button>
        </div>
      </div>

      {/* Mode toggle: today (live) ↔ full logs */}
      <div className="mb-5 inline-flex rounded-lg border border-ink-700 p-0.5">
        {(["today", "all"] as Mode[]).map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={"rounded-md px-4 py-1.5 text-sm font-medium transition-colors " + (mode === m ? "bg-brand text-white" : "text-slate-400 hover:text-white")}
          >
            {m === "today" ? t("logs.today") : t("logs.all")}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={() => load(mode, true)} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      {loading ? (
        <div className="rounded-lg border border-ink-800 px-4 py-10 text-center text-sm text-slate-500">{t("common.loading")}</div>
      ) : (
        <EventList events={events} emptyText={mode === "today" ? t("logs.noneToday") : t("logs.none")} />
      )}
    </div>
  );
}

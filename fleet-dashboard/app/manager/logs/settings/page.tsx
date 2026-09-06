"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getLogSettings, patchLogSettings, type LogSettingEvent, type LogSettings } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";

/**
 * Edit Logs — per event type: log it or not, and its limit. Backed by
 * GET/PATCH /log-settings (organizations.log_settings). Module-aware: the
 * backend only returns the types that apply to this org's module, so nothing
 * from the other module can appear here. Detection reads the same settings on
 * every ping batch, for school and university alike.
 */
export default function EditLogsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();
  const [data, setData] = useState<LogSettings | null>(null);
  const [draft, setDraft] = useState<Record<string, { enabled: boolean; threshold: string; duration_s: string }>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function seed(d: LogSettings) {
    setData(d);
    setDraft(
      Object.fromEntries(
        d.events.map((e) => [e.type, { enabled: e.enabled, threshold: e.threshold == null ? "" : String(e.threshold), duration_s: e.duration_s == null ? "" : String(e.duration_s) }]),
      ),
    );
  }

  useEffect(() => {
    getLogSettings()
      .then(seed)
      .catch((e) => setError(e instanceof Error ? e.message : t("common.loadFailed")))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    if (!data) return;
    setSaving(true);
    setError(null);
    try {
      const body: Record<string, { enabled?: boolean; threshold?: number; duration_s?: number }> = {};
      for (const e of data.events) {
        const v = draft[e.type];
        if (!v) continue;
        const entry: { enabled?: boolean; threshold?: number; duration_s?: number } = { enabled: v.enabled };
        if (e.default_threshold != null && v.threshold.trim() !== "") entry.threshold = Number(v.threshold);
        if (e.has_duration && v.duration_s.trim() !== "") entry.duration_s = Number(v.duration_s);
        body[e.type] = entry;
      }
      seed(await patchLogSettings(body));
      toast.success(t("editlogs.saved"));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  function resetDefaults() {
    if (!data) return;
    setDraft(
      Object.fromEntries(
        data.events.map((e) => [e.type, { enabled: true, threshold: e.default_threshold == null ? "" : String(e.default_threshold), duration_s: e.has_duration ? "60" : "" }]),
      ),
    );
  }

  const inputCls = "w-24 rounded-lg border border-ink-700 bg-ink-850 px-2 py-1.5 text-sm text-slate-100 focus:border-brand focus:outline-none disabled:opacity-50";

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("editlogs.title")}</h1>
          <p className="text-sm text-slate-400">{t("editlogs.subtitle")}</p>
        </div>
        {isSchool && (
          <Link href="/manager/logs" className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white">
            {t("editlogs.openLogs")}
          </Link>
        )}
      </div>

      {loading && <div className="text-slate-500">{t("common.loading")}</div>}
      {error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      {data && (
        <div className="space-y-4">
          {!data.configurable && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">{t("editlogs.locked")}</div>
          )}
          <div className="table-scroll rounded-xl border border-ink-800">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-4 py-3">{t("editlogs.event")}</th>
                  <th className="px-4 py-3">{t("editlogs.logged")}</th>
                  <th className="px-4 py-3">{t("editlogs.threshold")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {data.events.map((e: LogSettingEvent) => {
                  const v = draft[e.type];
                  if (!v) return null;
                  return (
                    <tr key={e.type} className={v.enabled ? "" : "opacity-60"}>
                      <td className="px-4 py-3 align-top">
                        <div className="font-medium text-white">{e.label}</div>
                        <div className="text-xs text-slate-500">{e.help}</div>
                      </td>
                      <td className="px-4 py-3 align-top">
                        <label className="inline-flex cursor-pointer items-center gap-2">
                          <input
                            type="checkbox"
                            checked={v.enabled}
                            disabled={!data.configurable}
                            onChange={(ev) => setDraft((d) => ({ ...d, [e.type]: { ...d[e.type], enabled: ev.target.checked } }))}
                            className="h-5 w-5 accent-[#3AA76D]"
                          />
                          <span className="text-sm text-slate-300">{v.enabled ? t("common.yes") : t("common.no")}</span>
                        </label>
                      </td>
                      <td className="px-4 py-3 align-top">
                        {e.default_threshold == null ? (
                          <span className="text-xs text-slate-500">{t("editlogs.noThreshold")}</span>
                        ) : (
                          <div className="flex flex-wrap items-center gap-2">
                            <input
                              type="number"
                              min={e.min ?? 0}
                              max={e.max ?? undefined}
                              value={v.threshold}
                              disabled={!data.configurable || !v.enabled}
                              onChange={(ev) => setDraft((d) => ({ ...d, [e.type]: { ...d[e.type], threshold: ev.target.value } }))}
                              className={inputCls}
                            />
                            <span className="text-sm text-slate-400">{e.unit}</span>
                            <span className="text-xs text-slate-600">({t("editlogs.default")} {e.default_threshold})</span>
                            {e.has_duration && (
                              <span className="flex items-center gap-1.5 text-sm text-slate-400">
                                · {t("editlogs.duration")}
                                <input
                                  type="number"
                                  min={0}
                                  max={900}
                                  value={v.duration_s}
                                  disabled={!data.configurable || !v.enabled}
                                  onChange={(ev) => setDraft((d) => ({ ...d, [e.type]: { ...d[e.type], duration_s: ev.target.value } }))}
                                  className={inputCls}
                                />
                                {t("editlogs.seconds")}
                              </span>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button type="button" onClick={resetDefaults} disabled={!data.configurable} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white disabled:opacity-50">
              {t("editlogs.resetDefaults")}
            </button>
            <Button onClick={save} loading={saving} disabled={!data.configurable} className="w-auto px-6">{t("common.save")}</Button>
          </div>
        </div>
      )}
    </div>
  );
}

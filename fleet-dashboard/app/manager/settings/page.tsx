"use client";

import { useEffect, useState } from "react";
import { getTrackingHours, setTrackingHours, type TrackingHours } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";

// Backend stores time as "HH:MM:SS"; the <input type=time> wants "HH:MM".
function toInput(t: string | null): string {
  return t ? t.slice(0, 5) : "";
}

export default function ManagerSettingsPage() {
  const { t } = useT();
  const [current, setCurrent] = useState<TrackingHours | null>(null);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [alwaysOn, setAlwaysOn] = useState(true);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getTrackingHours()
      .then((h) => {
        setCurrent(h);
        setStart(toInput(h.tracking_start_time));
        setEnd(toInput(h.tracking_end_time));
        setAlwaysOn(h.mode === "always_on");
      })
      .catch((e) => setError(e instanceof Error ? e.message : t("common.loadFailed")))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const h = alwaysOn
        ? await setTrackingHours(null, null)
        : await setTrackingHours(start, end);
      setCurrent(h);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="text-slate-500">{t("common.loading")}</div>;

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.settings")}</h1>
        <p className="text-sm text-slate-400">{t("settings.subtitle")}</p>
      </div>

      <form onSubmit={handleSave} className="space-y-4 rounded-xl border border-ink-800 bg-ink-900/50 p-5">
        <div>
          <h2 className="text-lg font-semibold text-white">{t("settings.trackingHours")}</h2>
          <p className="mt-1 text-sm text-slate-400">
            {t("settings.current")}:{" "}
            <span className="text-brand-sage">
              {current?.mode === "always_on"
                ? t("settings.alwaysOnLabel")
                : `${current?.tracking_start_time?.slice(0, 5)}–${current?.tracking_end_time?.slice(0, 5)}`}
            </span>
            .
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={alwaysOn}
            onChange={(e) => setAlwaysOn(e.target.checked)}
            className="h-4 w-4 accent-[#3AA76D]"
          />
          {t("settings.alwaysOnLabel")}
        </label>

        {!alwaysOn && (
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("settings.start")}</span>
              <input type="time" value={start} onChange={(e) => setStart(e.target.value)} required className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none" />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("settings.end")}</span>
              <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} required className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none" />
            </label>
          </div>
        )}

        <p className="text-xs text-slate-500">
          {alwaysOn ? t("settings.alwaysOnHelp") : t("settings.windowHelp")}
        </p>

        {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
        {saved && <div className="rounded-lg border border-brand/30 bg-brand/10 px-3 py-2 text-sm text-brand-sage">{t("settings.saved")}</div>}

        <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
      </form>
    </div>
  );
}

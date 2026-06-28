"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listAlerts,
  markAlertRead,
  listAlertRules,
  createAlertRule,
  updateAlertRule,
  deleteAlertRule,
  listVehicles,
  listDrivers,
  type ManagerAlert,
  type AlertRule,
  type ManagerVehicle,
  type ManagerDriver,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";

const ALERT_TYPES = ["speeding", "off_route", "short_stop", "offline"];
const THRESHOLD_LABEL: Record<string, string> = {
  speeding: "km/h",
  off_route: "meters",
  offline: "minutes",
  short_stop: "",
};

export default function ManagerAlertsPage() {
  const { t } = useT();
  const [tab, setTab] = useState<"alerts" | "rules">("alerts");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.alerts")}</h1>
        <p className="text-sm text-slate-400">{t("alerts.subtitle")}</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-ink-800">
        {(["alerts", "rules"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={
              "rounded-t-lg px-4 py-2 text-sm font-medium transition-colors " +
              (tab === key
                ? "border-b-2 border-brand text-white"
                : "text-slate-400 hover:text-white")
            }
          >
            {key === "alerts" ? t("alerts.tabAlerts") : t("alerts.tabRules")}
          </button>
        ))}
      </div>

      {tab === "alerts" ? <Feed /> : <RulesManager />}
    </div>
  );
}

function Feed() {
  const { t } = useT();
  const [alerts, setAlerts] = useState<ManagerAlert[]>([]);
  const [fType, setFType] = useState("");
  const [fRead, setFRead] = useState("");
  const [fDate, setFDate] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAlerts(
        await listAlerts({
          type: fType || undefined,
          is_read: fRead === "" ? undefined : fRead === "read",
          date: fDate || undefined,
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [fType, fRead, fDate]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleMarkRead(id: string) {
    try {
      await markAlertRead(id, true);
      setAlerts((a) => a.map((x) => (x.id === id ? { ...x, is_read: true } : x)));
      // Tell the layout to refresh the unread sidebar badge.
      window.dispatchEvent(new Event("fleet:alerts-changed"));
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.failed"));
    }
  }

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select value={fType} onChange={(e) => setFType(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-100">
          <option value="">{t("alerts.allTypes")}</option>
          {ALERT_TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
        </select>
        <select value={fRead} onChange={(e) => setFRead(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-100">
          <option value="">{t("alerts.readAndUnread")}</option>
          <option value="unread">{t("alerts.unread")}</option>
          <option value="read">{t("alerts.read")}</option>
        </select>
        <input type="date" value={fDate} onChange={(e) => setFDate(e.target.value)} className="rounded-lg border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-slate-100" />
      </div>

      {error && <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.type")}</th>
              <th className="px-4 py-3">{t("common.detail")}</th>
              <th className="px-4 py-3">{t("common.driver")}</th>
              <th className="px-4 py-3">{t("common.vehicle")}</th>
              <th className="px-4 py-3">{t("alerts.when")}</th>
              <th className="px-4 py-3 text-right">{t("alerts.read")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {loading && <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">{t("common.loading")}</td></tr>}
            {!loading && alerts.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">{t("alerts.noAlerts")}</td></tr>}
            {alerts.map((a) => (
              <tr key={a.id} className={a.is_read ? "" : "bg-amber-500/5"}>
                <td className="px-4 py-3"><span className="inline-flex rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs capitalize text-amber-300">{a.type.replace("_", " ")}</span></td>
                <td className="px-4 py-3 text-slate-300">{a.detail}</td>
                <td className="px-4 py-3 text-slate-400">{a.driver_name ?? "—"}</td>
                <td className="px-4 py-3 text-slate-400">{a.vehicle_bus_number ?? "—"}</td>
                <td className="px-4 py-3 text-xs text-slate-500">{a.occurred_at ? a.occurred_at.replace("T", " ").slice(0, 16) : "—"}</td>
                <td className="px-4 py-3 text-right">
                  {a.is_read ? (
                    <span className="text-xs text-slate-500">{t("alerts.read")}</span>
                  ) : (
                    <button onClick={() => handleMarkRead(a.id)} className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-slate-300 hover:border-brand hover:text-white">{t("alerts.markRead")}</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RulesManager() {
  const { t } = useT();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState("speeding");
  const [threshold, setThreshold] = useState("");
  const [targetKind, setTargetKind] = useState<"all" | "vehicles" | "drivers">("all");
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [vehicles, setVehicles] = useState<ManagerVehicle[]>([]);
  const [drivers, setDrivers] = useState<ManagerDriver[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setRules(await listAlertRules());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  async function openModal() {
    setName(""); setType("speeding"); setThreshold(""); setTargetKind("all"); setTargetIds([]); setCreateError(null);
    setOpen(true);
    try {
      const [v, d] = await Promise.all([listVehicles(), listDrivers()]);
      setVehicles(v); setDrivers(d);
    } catch { /* targets stay empty */ }
  }

  function toggleId(id: string) {
    setTargetIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true); setCreateError(null);
    try {
      await createAlertRule({
        name: name.trim(), type,
        threshold: type === "short_stop" ? null : threshold ? Number(threshold) : null,
        target_kind: targetKind,
        target_ids: targetKind === "all" ? null : targetIds,
        notify_panel: true, notify_email: false, notify_push: false, is_active: true,
      });
      setOpen(false); await reload();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  async function toggleRule(r: AlertRule) {
    try { await updateAlertRule(r.id, { is_active: !r.is_active }); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : t("common.failed")); }
  }
  async function removeRule(r: AlertRule) {
    if (!window.confirm(`${t("common.delete")} "${r.name}"?`)) return;
    try { await deleteAlertRule(r.id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : t("common.failed")); }
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">{t("alerts.rulesTitle")}</h2>
        <button onClick={openModal} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">+ {t("alerts.newRule")}</button>
      </div>

      {error && <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("common.type")}</th>
              <th className="px-4 py-3">{t("alerts.threshold")}</th>
              <th className="px-4 py-3">{t("alerts.target")}</th>
              <th className="px-4 py-3">{t("common.active")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {rules.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">{t("alerts.noRules")}</td></tr>}
            {rules.map((r) => (
              <tr key={r.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 text-white">{r.name}</td>
                <td className="px-4 py-3 capitalize text-slate-300">{r.type.replace("_", " ")}</td>
                <td className="px-4 py-3 text-slate-300">{r.threshold ?? "—"}</td>
                <td className="px-4 py-3 capitalize text-slate-400">{r.target_kind}{r.target_ids && r.target_ids.length ? ` (${r.target_ids.length})` : ""}</td>
                <td className="px-4 py-3">
                  <button onClick={() => toggleRule(r)} className={`rounded-full px-2.5 py-0.5 text-xs ${r.is_active ? "bg-brand/15 text-brand-sage" : "bg-ink-800 text-slate-400"}`}>
                    {r.is_active ? t("common.active") : t("alerts.ruleOff")}
                  </button>
                </td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => removeRule(r)} className="rounded-md border border-red-500/40 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/10">{t("common.delete")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={t("alerts.newRule")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Input label={`${t("common.name")} *`} value={name} onChange={(e) => setName(e.target.value)} required />
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("common.type")}</span>
              <select value={type} onChange={(e) => setType(e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none">
                {ALERT_TYPES.map((ty) => <option key={ty} value={ty}>{ty}</option>)}
              </select>
            </label>
            {type !== "short_stop" && (
              <Input label={`${t("alerts.threshold")} (${THRESHOLD_LABEL[type]})`} type="number" min={0} step="any" value={threshold} onChange={(e) => setThreshold(e.target.value)} required />
            )}
          </div>
          {type === "short_stop" && <p className="text-xs text-slate-500">{t("alerts.shortStopNote")}</p>}

          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("alerts.appliesTo")}</span>
            <select value={targetKind} onChange={(e) => { setTargetKind(e.target.value as typeof targetKind); setTargetIds([]); }} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none">
              <option value="all">{t("alerts.targetAll")}</option>
              <option value="vehicles">{t("alerts.targetVehicles")}</option>
              <option value="drivers">{t("alerts.targetDrivers")}</option>
            </select>
          </label>

          {targetKind !== "all" && (
            <div className="max-h-36 overflow-auto rounded-lg border border-ink-800 p-2">
              {(targetKind === "vehicles" ? vehicles.map((v) => ({ id: v.id, label: v.bus_number })) : drivers.map((d) => ({ id: d.id, label: d.name }))).map((o) => (
                <label key={o.id} className="flex items-center gap-2 py-1 text-sm text-slate-300">
                  <input type="checkbox" checked={targetIds.includes(o.id)} onChange={() => toggleId(o.id)} className="h-4 w-4 accent-[#3AA76D]" />
                  {o.label}
                </label>
              ))}
            </div>
          )}

          <div>
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("alerts.notifyVia")}</span>
            <div className="flex flex-wrap gap-4 text-sm">
              <label className="flex items-center gap-2 text-slate-300"><input type="checkbox" checked readOnly className="h-4 w-4 accent-[#3AA76D]" /> {t("alerts.panel")}</label>
              <label className="flex items-center gap-2 text-slate-500"><input type="checkbox" disabled className="h-4 w-4" /> {t("common.email")} <span className="text-xs">({t("alerts.comingSoon")})</span></label>
              <label className="flex items-center gap-2 text-slate-500"><input type="checkbox" disabled className="h-4 w-4" /> Push <span className="text-xs">({t("alerts.comingSoon")})</span></label>
            </div>
          </div>

          {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
          </div>
        </form>
      </Modal>
    </section>
  );
}

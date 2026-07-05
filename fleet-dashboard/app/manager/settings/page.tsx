"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getTrackingHours,
  setTrackingHours,
  listUsers,
  createUser,
  updateUser,
  MANAGER_PERMISSIONS,
  type TrackingHours,
  type ManagerUser,
  type StaffRole,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import CentersManager from "@/components/CentersManager";

function toInput(s: string | null): string {
  return s ? s.slice(0, 5) : "";
}

const ROLE_DEFAULTS: Record<StaffRole, string[]> = {
  manager: [...MANAGER_PERMISSIONS],
  dispatcher: ["manage_routes", "manage_trips", "view_tracking", "view_reports"],
  viewer: ["view_tracking", "view_reports"],
};

function permsFor(role: StaffRole): Record<string, boolean> {
  return Object.fromEntries(MANAGER_PERMISSIONS.map((p) => [p, ROLE_DEFAULTS[role].includes(p)]));
}

export default function ManagerSettingsPage() {
  const { t } = useT();
  const [tab, setTab] = useState<"tracking" | "users" | "centers">("tracking");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white">{t("nav.settings")}</h1>
        <p className="text-sm text-slate-400">{t("settings.subtitle")}</p>
      </div>

      <div className="flex gap-1 border-b border-ink-800">
        {(["tracking", "users", "centers"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={"rounded-t-lg px-4 py-2 text-sm font-medium transition-colors " + (tab === key ? "border-b-2 border-brand text-white" : "text-slate-400 hover:text-white")}
          >
            {key === "tracking" ? t("settings.tabTracking") : key === "users" ? t("settings.tabUsers") : t("settings.tabCenters")}
          </button>
        ))}
      </div>

      {tab === "tracking" ? <TrackingTab /> : tab === "users" ? <UsersTab /> : <CentersManager />}
    </div>
  );
}

function TrackingTab() {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const h = alwaysOn ? await setTrackingHours(null, null) : await setTrackingHours(start, end);
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
    <form onSubmit={handleSave} className="max-w-xl space-y-4 rounded-xl border border-ink-800 bg-ink-900/50 p-5">
      <div>
        <h2 className="text-lg font-semibold text-white">{t("settings.trackingHours")}</h2>
        <p className="mt-1 text-sm text-slate-400">
          {t("settings.current")}:{" "}
          <span className="text-brand-sage">
            {current?.mode === "always_on" ? t("settings.alwaysOnLabel") : `${current?.tracking_start_time?.slice(0, 5)}–${current?.tracking_end_time?.slice(0, 5)}`}
          </span>
          .
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input type="checkbox" checked={alwaysOn} onChange={(e) => setAlwaysOn(e.target.checked)} className="h-4 w-4 accent-[#3AA76D]" />
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

      <p className="text-xs text-slate-500">{alwaysOn ? t("settings.alwaysOnHelp") : t("settings.windowHelp")}</p>
      {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
      {saved && <div className="rounded-lg border border-brand/30 bg-brand/10 px-3 py-2 text-sm text-brand-sage">{t("settings.saved")}</div>}
      <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
    </form>
  );
}

function roleLabel(t: (k: string) => string, role: string): string {
  return role === "manager" ? t("users.roleManager") : role === "dispatcher" ? t("users.roleDispatcher") : t("users.roleViewer");
}

function UsersTab() {
  const { t } = useT();
  const toast = useToast();
  const [users, setUsers] = useState<ManagerUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // create
  const [createOpen, setCreateOpen] = useState(false);
  const [cForm, setCForm] = useState({ name: "", username: "", password: "", role: "dispatcher" as StaffRole });
  const [cPerms, setCPerms] = useState<Record<string, boolean>>(permsFor("dispatcher"));
  const [creating, setCreating] = useState(false);
  const [cError, setCError] = useState<string | null>(null);

  // edit
  const [editUser, setEditUser] = useState<ManagerUser | null>(null);
  const [eName, setEName] = useState("");
  const [eRole, setERole] = useState<StaffRole>("dispatcher");
  const [ePerms, setEPerms] = useState<Record<string, boolean>>({});
  const [eActive, setEActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  function openCreate() {
    setCForm({ name: "", username: "", password: "", role: "dispatcher" });
    setCPerms(permsFor("dispatcher"));
    setCError(null);
    setCreateOpen(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCError(null);
    try {
      await createUser({ name: cForm.name.trim(), username: cForm.username.trim(), password: cForm.password, role: cForm.role, permissions: cPerms });
      setCreateOpen(false);
      toast.success(t("toast.created"));
      await reload();
    } catch (err) {
      setCError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  function openEdit(u: ManagerUser) {
    setEditUser(u);
    setEName(u.name);
    setERole(u.role);
    setEPerms(Object.fromEntries(MANAGER_PERMISSIONS.map((p) => [p, u.permissions?.[p] === true])));
    setEActive(u.is_active);
    setEError(null);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editUser) return;
    setSaving(true);
    setEError(null);
    try {
      await updateUser(editUser.id, { name: eName.trim(), role: eRole, is_active: eActive, permissions: ePerms });
      setEditUser(null);
      toast.success(t("toast.saved"));
      await reload();
    } catch (err) {
      setEError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-sm text-slate-400">{t("settings.usersSubtitle")}</p>
        <button onClick={openCreate} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">+ {t("users.newUser2")}</button>
      </div>

      {error && <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("common.username")}</th>
              <th className="px-4 py-3">{t("common.role")}</th>
              <th className="px-4 py-3">{t("users.permissions")}</th>
              <th className="px-4 py-3">{t("common.active")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {loading && <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">{t("common.loading")}</td></tr>}
            {!loading && users.length === 0 && <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-500">{t("users.none")}</td></tr>}
            {users.map((u) => {
              const active = Object.entries(u.permissions || {}).filter(([, v]) => v).map(([k]) => k);
              return (
                <tr key={u.id} className="hover:bg-ink-900/40">
                  <td className="px-4 py-3 font-medium text-white">{u.name}</td>
                  <td className="px-4 py-3 text-slate-300">{u.username}</td>
                  <td className="px-4 py-3 text-slate-300">{roleLabel(t, u.role)}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{active.length}</td>
                  <td className="px-4 py-3">
                    <span className={"rounded-full px-2.5 py-0.5 text-xs " + (u.is_active ? "bg-brand/15 text-brand-sage" : "bg-ink-800 text-slate-400")}>
                      {u.is_active ? t("common.active") : t("common.inactive")}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => openEdit(u)} title={t("users.editUser")} aria-label={t("users.editUser")} className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" /></svg>
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Create */}
      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title={t("users.newUser2")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Input label={`${t("common.name")} *`} value={cForm.name} onChange={(e) => setCForm((f) => ({ ...f, name: e.target.value }))} required />
          <div className="grid grid-cols-2 gap-3">
            <Input label={`${t("common.username")} *`} value={cForm.username} onChange={(e) => setCForm((f) => ({ ...f, username: e.target.value }))} required />
            <Input label={`${t("common.password")} *`} type="password" value={cForm.password} onChange={(e) => setCForm((f) => ({ ...f, password: e.target.value }))} required />
          </div>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("common.role")}</span>
            <select value={cForm.role} onChange={(e) => { const r = e.target.value as StaffRole; setCForm((f) => ({ ...f, role: r })); setCPerms(permsFor(r)); }} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none">
              <option value="manager">{t("users.roleManager")}</option>
              <option value="dispatcher">{t("users.roleDispatcher")}</option>
              <option value="viewer">{t("users.roleViewer")}</option>
            </select>
          </label>
          <div>
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("users.permissions")}</span>
            <PermChecks value={cPerms} onChange={setCPerms} />
          </div>
          {cError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{cError}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setCreateOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
            <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
          </div>
        </form>
      </Modal>

      {/* Edit */}
      <Modal open={editUser !== null} onClose={() => setEditUser(null)} title={t("users.editUser")}>
        {editUser && (
          <form onSubmit={handleEdit} className="space-y-3">
            <Input label={t("common.name")} value={eName} onChange={(e) => setEName(e.target.value)} required />
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("common.role")}</span>
              <select value={eRole} onChange={(e) => setERole(e.target.value as StaffRole)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none">
                <option value="manager">{t("users.roleManager")}</option>
                <option value="dispatcher">{t("users.roleDispatcher")}</option>
                <option value="viewer">{t("users.roleViewer")}</option>
              </select>
            </label>
            <div>
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("users.permissions")}</span>
              <PermChecks value={ePerms} onChange={setEPerms} />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={eActive} onChange={(e) => setEActive(e.target.checked)} className="h-4 w-4 accent-[#3AA76D]" />
              {t("common.active")}
            </label>
            {eError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{eError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setEditUser(null)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </section>
  );
}

function PermChecks({ value, onChange }: { value: Record<string, boolean>; onChange: (next: Record<string, boolean>) => void }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {MANAGER_PERMISSIONS.map((p) => (
        <label key={p} className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={!!value[p]} onChange={(e) => onChange({ ...value, [p]: e.target.checked })} className="h-4 w-4 accent-[#3AA76D]" />
          {p}
        </label>
      ))}
    </div>
  );
}

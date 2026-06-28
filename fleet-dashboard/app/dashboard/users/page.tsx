"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listPlatformUsers,
  createPlatformUser,
  updatePlatformUser,
  deletePlatformUser,
  PLATFORM_PERMISSIONS,
  type PlatformUser,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";

function emptyPerms(): Record<string, boolean> {
  return Object.fromEntries(PLATFORM_PERMISSIONS.map((p) => [p, false]));
}

function PermChecks({
  value,
  onChange,
}: {
  value: Record<string, boolean>;
  onChange: (next: Record<string, boolean>) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {PLATFORM_PERMISSIONS.map((p) => (
        <label key={p} className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={!!value[p]}
            onChange={(e) => onChange({ ...value, [p]: e.target.checked })}
            className="h-4 w-4 accent-[#3AA76D]"
          />
          {p}
        </label>
      ))}
    </div>
  );
}

export default function PlatformUsersPage() {
  const { t } = useT();
  const [users, setUsers] = useState<PlatformUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [cForm, setCForm] = useState({ name: "", email: "", password: "" });
  const [cPerms, setCPerms] = useState<Record<string, boolean>>(emptyPerms());
  const [creating, setCreating] = useState(false);
  const [cError, setCError] = useState<string | null>(null);

  const [editUser, setEditUser] = useState<PlatformUser | null>(null);
  const [ePerms, setEPerms] = useState<Record<string, boolean>>(emptyPerms());
  const [eActive, setEActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listPlatformUsers());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setCForm({ name: "", email: "", password: "" });
    setCPerms(emptyPerms());
    setCError(null);
    setCreateOpen(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCError(null);
    try {
      await createPlatformUser({
        name: cForm.name.trim(),
        email: cForm.email.trim(),
        password: cForm.password,
        permissions: cPerms,
      });
      setCreateOpen(false);
      await load();
    } catch (err) {
      setCError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  function openEdit(u: PlatformUser) {
    setEditUser(u);
    setEPerms({ ...emptyPerms(), ...u.permissions });
    setEActive(u.is_active);
    setEError(null);
  }

  async function handleSave() {
    if (!editUser) return;
    setSaving(true);
    setEError(null);
    try {
      await updatePlatformUser(editUser.id, { permissions: ePerms, is_active: eActive });
      setEditUser(null);
      await load();
    } catch (err) {
      setEError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(u: PlatformUser) {
    if (!window.confirm(`${t("users.deleteConfirmPre")} "${u.name}" (${u.email})${t("users.deleteConfirmPost")}`)) {
      return;
    }
    try {
      await deletePlatformUser(u.id);
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : t("common.failed"));
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.users")}</h1>
          <p className="text-sm text-slate-400">{t("users.subtitle")}</p>
        </div>
        <button
          onClick={openCreate}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
        >
          + {t("users.newUser")}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("common.email")}</th>
              <th className="px-4 py-3">{t("users.permissions")}</th>
              <th className="px-4 py-3">{t("common.active")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {loading && <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">Loading…</td></tr>}
            {users.map((u) => {
              const active = Object.entries(u.permissions || {}).filter(([, v]) => v).map(([k]) => k);
              return (
                <tr key={u.id} className="hover:bg-ink-900/40">
                  <td className="px-4 py-3 text-white">{u.name}</td>
                  <td className="px-4 py-3 text-slate-400">{u.email}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {active.length ? active.join(", ") : "—"}
                  </td>
                  <td className="px-4 py-3">
                    <span className={u.is_active ? "text-brand-sage" : "text-slate-500"}>
                      {u.is_active ? t("common.yes") : t("common.no")}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1.5">
                      <button onClick={() => openEdit(u)} className="rounded-md border border-ink-700 px-2.5 py-1 text-xs text-slate-300 hover:border-brand hover:text-white">{t("common.edit")}</button>
                      <button onClick={() => handleDelete(u)} className="rounded-md border border-red-500/40 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/10">{t("common.delete")}</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Create */}
      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title={t("users.newPlatformUser")}>
        <form onSubmit={handleCreate} className="space-y-3">
          <Input label={`${t("common.name")} *`} value={cForm.name} onChange={(e) => setCForm((f) => ({ ...f, name: e.target.value }))} required />
          <Input label={`${t("common.email")} *`} type="email" value={cForm.email} onChange={(e) => setCForm((f) => ({ ...f, email: e.target.value }))} required />
          <Input label={`${t("common.password")} *`} type="password" value={cForm.password} onChange={(e) => setCForm((f) => ({ ...f, password: e.target.value }))} required minLength={6} />
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
      <Modal open={editUser !== null} onClose={() => setEditUser(null)} title={editUser ? `${t("common.edit")} ${editUser.name}` : ""}>
        {editUser && (
          <div className="space-y-4">
            <div>
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("users.permissions")}</span>
              <PermChecks value={ePerms} onChange={setEPerms} />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={eActive} onChange={(e) => setEActive(e.target.checked)} className="h-4 w-4 accent-[#3AA76D]" />
              {t("common.activeHdr")}
            </label>
            {eError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{eError}</div>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setEditUser(null)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="button" onClick={handleSave} loading={saving} className="w-auto px-6">{t("common.save")}</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

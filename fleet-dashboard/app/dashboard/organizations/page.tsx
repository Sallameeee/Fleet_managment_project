"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  listOrganizations,
  createOrganization,
  getOrganization,
  deleteOrganization,
  impersonateOrganization,
  type Organization,
  type CreateOrgInput,
  type OrgProfile,
} from "@/lib/api";
import { startImpersonation } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";

const EMPTY_FORM = {
  name: "",
  username: "",
  password: "",
  address: "",
  email: "",
  phone: "",
  plan: "basic",
  max_devices: "10",
  monthly_fee: "0",
  subscription_expiry: "",
};

function money(n: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function OrganizationsPage() {
  const router = useRouter();
  const { t } = useT();
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [createdLogin, setCreatedLogin] = useState<string | null>(null);

  // View drivers/users
  const [viewOrg, setViewOrg] = useState<Organization | null>(null);
  const [viewMode, setViewMode] = useState<"drivers" | "users">("drivers");
  const [viewRows, setViewRows] = useState<OrgProfile[] | null>(null);
  const [viewError, setViewError] = useState<string | null>(null);

  // Delete + impersonate
  const [deleteOrg, setDeleteOrg] = useState<Organization | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [impersonatingId, setImpersonatingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOrgs(await listOrganizations());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openModal() {
    setForm({ ...EMPTY_FORM });
    setCreateError(null);
    setCreatedLogin(null);
    setModalOpen(true);
  }

  function update(field: keyof typeof EMPTY_FORM, value: string) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    setCreatedLogin(null);
    try {
      const payload: CreateOrgInput = {
        name: form.name.trim(),
        username: form.username.trim(),
        password: form.password,
        plan: form.plan as CreateOrgInput["plan"],
        max_devices: Number(form.max_devices) || 0,
        monthly_fee: Number(form.monthly_fee) || 0,
      };
      if (form.address.trim()) payload.address = form.address.trim();
      if (form.email.trim()) payload.email = form.email.trim();
      if (form.phone.trim()) payload.phone = form.phone.trim();
      if (form.subscription_expiry) payload.subscription_expiry = form.subscription_expiry;

      const result = await createOrganization(payload);
      setCreatedLogin(result.owner.login);
      await load();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  async function openView(org: Organization, mode: "drivers" | "users") {
    setViewOrg(org);
    setViewMode(mode);
    setViewRows(null);
    setViewError(null);
    try {
      const detail = await getOrganization(org.id);
      const rows =
        mode === "drivers"
          ? detail.profiles.filter((p) => p.role === "driver")
          : detail.profiles.filter((p) => p.role !== "driver");
      setViewRows(rows);
    } catch (e) {
      setViewError(e instanceof Error ? e.message : t("common.loadFailed"));
    }
  }

  async function handleImpersonate(org: Organization) {
    setImpersonatingId(org.id);
    try {
      const res = await impersonateOrganization(org.id);
      startImpersonation(res.access_token, res.impersonation);
      // Full navigation so the manager layout reads the new session cleanly.
      window.location.assign("/manager");
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.failed"));
      setImpersonatingId(null);
    }
  }

  async function handleDelete() {
    if (!deleteOrg) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteOrganization(deleteOrg.id);
      setDeleteOrg(null);
      setConfirmText("");
      await load();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("nav.organizations")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${orgs.length} ${t("orgs.total")}`}</p>
        </div>
        <button
          onClick={openModal}
          className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage"
        >
          + {t("orgs.newOrg")}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("common.name")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3">{t("orgs.plan")}</th>
              <th className="px-4 py-3">{t("orgs.monthlyFee")}</th>
              <th className="px-4 py-3">{t("orgs.expiry")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {loading && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">{t("common.loading")}</td></tr>
            )}
            {!loading && orgs.length === 0 && !error && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">{t("common.none")}</td></tr>
            )}
            {orgs.map((o) => (
              <tr
                key={o.id}
                onClick={() => router.push(`/dashboard/organizations/${o.id}`)}
                className="cursor-pointer hover:bg-ink-900/40"
              >
                <td className="px-4 py-3">
                  <div className="font-medium text-white">{o.name}</div>
                  <div className="text-xs text-slate-500">{o.slug}</div>
                </td>
                <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                <td className="px-4 py-3 capitalize text-slate-300">{o.plan}</td>
                <td className="px-4 py-3 text-slate-300">{money(o.monthly_fee)}</td>
                <td className="px-4 py-3 text-slate-300">{o.subscription_expiry ?? "—"}</td>
                <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center justify-end gap-1.5">
                    <ActionBtn onClick={() => openView(o, "drivers")} title={t("orgs.viewDrivers")}>{t("nav.drivers")}</ActionBtn>
                    <ActionBtn onClick={() => openView(o, "users")} title={t("orgs.viewUsers")}>{t("nav.users")}</ActionBtn>
                    <ActionBtn
                      onClick={() => handleImpersonate(o)}
                      title={t("orgs.loginAs")}
                      accent
                    >
                      {impersonatingId === o.id ? "…" : t("orgs.login")}
                    </ActionBtn>
                    <ActionBtn onClick={() => { setDeleteOrg(o); setConfirmText(""); setDeleteError(null); }} title={t("orgs.deleteOrg")} danger>
                      {t("common.delete")}
                    </ActionBtn>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* View drivers/users */}
      <Modal
        open={viewOrg !== null}
        onClose={() => setViewOrg(null)}
        title={viewOrg ? `${viewOrg.name} — ${viewMode === "drivers" ? t("nav.drivers") : t("nav.users")}` : ""}
      >
        {viewError && <div className="text-sm text-red-300">{viewError}</div>}
        {!viewError && viewRows === null && <div className="text-slate-500">{t("common.loading")}</div>}
        {viewRows && viewRows.length === 0 && (
          <div className="text-slate-500">{t("orgs.noneInOrg")}</div>
        )}
        {viewRows && viewRows.length > 0 && (
          <ul className="divide-y divide-ink-800">
            {viewRows.map((p) => (
              <li key={p.id} className="flex items-center justify-between py-2 text-sm">
                <span className="text-white">{p.name}</span>
                <span className="text-slate-400">
                  {p.username} · <span className="capitalize">{p.role}</span>
                  {p.is_active ? "" : " · inactive"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Modal>

      {/* Strong delete confirm */}
      <Modal open={deleteOrg !== null} onClose={() => setDeleteOrg(null)} title={t("orgs.deleteOrgTitle")}>
        {deleteOrg && (
          <div className="space-y-4">
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
              {t("orgs.deleteWarnPre")} <strong>{deleteOrg.name}</strong> {t("orgs.deleteWarnPost")}
            </div>
            <label className="block text-sm">
              <span className="mb-1.5 block text-slate-300">
                {t("orgs.deleteTypePre")} <span className="font-mono text-white">{deleteOrg.name}</span> {t("orgs.deleteTypePost")}
              </span>
              <input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-red-500 focus:outline-none"
              />
            </label>
            {deleteError && <div className="text-sm text-red-300">{deleteError}</div>}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteOrg(null)}
                className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={handleDelete}
                disabled={confirmText !== deleteOrg.name || deleting}
                className="rounded-lg bg-red-600 px-5 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {deleting ? t("orgs.deleting") : t("orgs.deletePermanently")}
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* Create */}
      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={t("orgs.newOrg")}>
        {createdLogin ? (
          <div className="space-y-4">
            <div className="rounded-lg border border-brand/30 bg-brand/10 px-4 py-3 text-sm text-brand-sage">
              {t("orgs.created")}
              <div className="mt-2 select-all font-mono text-base text-white">{createdLogin}</div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={openModal} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">
                {t("common.createAnother")}
              </button>
              <Button type="button" onClick={() => setModalOpen(false)} className="w-auto px-4">{t("common.done")}</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Input label={`${t("common.name")} *`} value={form.name} onChange={(e) => update("name", e.target.value)} required />
              <Input label={`${t("orgs.ownerUsername")} *`} value={form.username} onChange={(e) => update("username", e.target.value)} required />
            </div>
            <Input label={`${t("orgs.ownerPassword")} *`} type="password" value={form.password} onChange={(e) => update("password", e.target.value)} required minLength={6} />
            <div className="grid grid-cols-2 gap-3">
              <Input label={t("common.email")} type="email" value={form.email} onChange={(e) => update("email", e.target.value)} />
              <Input label={t("common.phone")} value={form.phone} onChange={(e) => update("phone", e.target.value)} />
            </div>
            <Input label={t("common.address")} value={form.address} onChange={(e) => update("address", e.target.value)} />
            <div className="grid grid-cols-3 gap-3">
              <label className="block">
                <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("orgs.plan")}</span>
                <select value={form.plan} onChange={(e) => update("plan", e.target.value)} className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40">
                  <option value="basic">basic</option>
                  <option value="pro">pro</option>
                  <option value="enterprise">enterprise</option>
                </select>
              </label>
              <Input label={t("common.maxDevices")} type="number" min={0} value={form.max_devices} onChange={(e) => update("max_devices", e.target.value)} />
              <Input label={t("orgs.monthlyFee")} type="number" min={0} step="0.01" value={form.monthly_fee} onChange={(e) => update("monthly_fee", e.target.value)} />
            </div>
            <Input label={t("orgs.subscriptionExpiry")} type="date" value={form.subscription_expiry} onChange={(e) => update("subscription_expiry", e.target.value)} />
            {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setModalOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={creating} className="w-auto px-6">{t("common.create")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

function ActionBtn({
  children,
  onClick,
  title,
  danger,
  accent,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title: string;
  danger?: boolean;
  accent?: boolean;
}) {
  const tone = danger
    ? "border-red-500/40 text-red-300 hover:bg-red-500/10"
    : accent
    ? "border-brand/40 text-brand-sage hover:bg-brand/10"
    : "border-ink-700 text-slate-300 hover:border-brand hover:text-white";
  return (
    <button title={title} onClick={onClick} className={`rounded-md border px-2.5 py-1 text-xs ${tone}`}>
      {children}
    </button>
  );
}

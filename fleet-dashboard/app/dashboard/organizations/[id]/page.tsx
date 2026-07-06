"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  getOrganization,
  updateOrganization,
  setOrganizationStatus,
  type OrganizationDetail,
  type OrgPatch,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";

function money(n: number): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const router = useRouter();
  const { t } = useT();

  const [org, setOrg] = useState<OrganizationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [form, setForm] = useState({ plan: "basic", module: "university", max_devices: "", monthly_fee: "", subscription_expiry: "" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busyStatus, setBusyStatus] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOrg(await getOrganization(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  function openEdit() {
    if (!org) return;
    setForm({
      plan: org.plan as string,
      module: (org.module as string) ?? "university",
      max_devices: String(org.max_devices ?? ""),
      monthly_fee: String(org.monthly_fee ?? ""),
      subscription_expiry: org.subscription_expiry ?? "",
    });
    setSaveError(null);
    setEditOpen(true);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const patch: OrgPatch = {
        plan: form.plan as OrgPatch["plan"],
        module: form.module as OrgPatch["module"],
        max_devices: Number(form.max_devices) || 0,
        monthly_fee: Number(form.monthly_fee) || 0,
        subscription_expiry: form.subscription_expiry || null,
      };
      await updateOrganization(id, patch);
      setEditOpen(false);
      await load();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function toggleStatus() {
    if (!org) return;
    const next = org.status === "active" ? "suspended" : "active";
    const verb = next === "suspended" ? t("orgs.suspend") : t("orgs.activate");
    if (!window.confirm(`${verb} "${org.name}"? ${next === "suspended" ? t("orgsd.usersFrozen") : ""}`)) {
      return;
    }
    setBusyStatus(true);
    try {
      await setOrganizationStatus(id, next);
      await load();
    } catch (e) {
      alert(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setBusyStatus(false);
    }
  }

  if (loading) return <div className="text-slate-500">{t("common.loading")}</div>;
  if (error)
    return (
      <div>
        <Link href="/dashboard/organizations" className="text-sm text-slate-400 hover:text-white">
          ← {t("nav.organizations")}
        </Link>
        <div className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      </div>
    );
  if (!org) return null;

  const suspended = org.status === "suspended" || org.status === "expired";

  return (
    <div className="space-y-6">
      <Link href="/dashboard/organizations" className="text-sm text-slate-400 hover:text-white">
        ← {t("nav.organizations")}
      </Link>

      {suspended && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {t("orgsd.bannerPre")} <strong><StatusBadge status={org.status} /></strong> {t("orgsd.bannerPost")}
        </div>
      )}

      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold text-white">{org.name}</h1>
            <StatusBadge status={org.status} />
          </div>
          <p className="text-sm text-slate-500">{org.slug}</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={openEdit}
            className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
          >
            {t("orgsd.editSubscription")}
          </button>
          <button
            onClick={toggleStatus}
            disabled={busyStatus}
            className={
              "rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-60 " +
              (org.status === "active" ? "bg-red-600 hover:bg-red-500" : "bg-brand hover:bg-brand-sage")
            }
          >
            {org.status === "active" ? t("orgs.suspend") : t("orgs.activate")}
          </button>
        </div>
      </div>

      {/* Info grid */}
      <div className="grid grid-cols-2 gap-4 rounded-xl border border-ink-800 bg-ink-900/40 p-5 text-sm sm:grid-cols-4">
        <Info label={t("orgs.plan")} value={org.plan} />
        <Info label={t("orgs.module")} value={org.module === "school" ? t("orgs.moduleSchool") : t("orgs.moduleUniversity")} />
        <Info label={t("orgs.monthlyFee")} value={money(org.monthly_fee)} />
        <Info label={t("common.maxDevices")} value={String(org.max_devices)} />
        <Info label={t("orgs.expiry")} value={org.subscription_expiry ?? "—"} />
        <Info label={t("common.email")} value={org.email ?? "—"} />
        <Info label={t("common.phone")} value={org.phone ?? "—"} />
        <Info label={t("common.address")} value={org.address ?? "—"} />
        <Info label={t("orgsd.driversVehicles")} value={`${org.counts?.drivers ?? 0} / ${org.counts?.vehicles ?? 0}`} />
      </div>

      {/* Users */}
      <section>
        <h2 className="mb-2 text-lg font-semibold text-white">{t("nav.users")} ({org.profiles.length})</h2>
        <div className="overflow-hidden rounded-xl border border-ink-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2.5">{t("common.name")}</th>
                <th className="px-4 py-2.5">{t("common.username")}</th>
                <th className="px-4 py-2.5">{t("common.role")}</th>
                <th className="px-4 py-2.5">{t("common.activeHdr")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {org.profiles.map((p) => (
                <tr key={p.id}>
                  <td className="px-4 py-2.5 text-white">{p.name}</td>
                  <td className="px-4 py-2.5 text-slate-400">{p.username}</td>
                  <td className="px-4 py-2.5 capitalize text-slate-300">{p.role}</td>
                  <td className="px-4 py-2.5">
                    <span className={p.is_active ? "text-brand-sage" : "text-slate-500"}>
                      {p.is_active ? t("common.yes") : t("common.no")}
                    </span>
                  </td>
                </tr>
              ))}
              {org.profiles.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-slate-500">{t("common.none")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Vehicles */}
      <section>
        <h2 className="mb-2 text-lg font-semibold text-white">{t("nav.vehicles")} ({org.vehicles.length})</h2>
        <div className="overflow-hidden rounded-xl border border-ink-800">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2.5">{t("vehicles.busNumber")}</th>
                <th className="px-4 py-2.5">{t("vehicles.plate")}</th>
                <th className="px-4 py-2.5">{t("common.activeHdr")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800">
              {org.vehicles.map((v) => (
                <tr key={v.id}>
                  <td className="px-4 py-2.5 text-slate-200">{v.bus_number}</td>
                  <td className="px-4 py-2.5 text-slate-400">{v.plate_number ?? "—"}</td>
                  <td className="px-4 py-2.5">
                    <span className={v.is_active ? "text-brand-sage" : "text-slate-500"}>
                      {v.is_active ? t("common.yes") : t("common.no")}
                    </span>
                  </td>
                </tr>
              ))}
              {org.vehicles.length === 0 && (
                <tr>
                  <td colSpan={3} className="px-4 py-6 text-center text-slate-500">{t("common.none")}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Edit subscription modal */}
      <Modal open={editOpen} onClose={() => setEditOpen(false)} title={t("orgsd.editSubscription")}>
        <form onSubmit={handleSave} className="space-y-3">
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("orgs.module")}</span>
            <select
              value={form.module}
              onChange={(e) => setForm((f) => ({ ...f, module: e.target.value }))}
              className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40"
            >
              <option value="university">{t("orgs.moduleUniversity")}</option>
              <option value="school">{t("orgs.moduleSchool")}</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("orgs.plan")}</span>
            <select
              value={form.plan}
              onChange={(e) => setForm((f) => ({ ...f, plan: e.target.value }))}
              className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40"
            >
              <option value="basic">basic</option>
              <option value="pro">pro</option>
              <option value="enterprise">enterprise</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label={t("common.maxDevices")}
              type="number"
              min={0}
              value={form.max_devices}
              onChange={(e) => setForm((f) => ({ ...f, max_devices: e.target.value }))}
            />
            <Input
              label={t("orgs.monthlyFee")}
              type="number"
              min={0}
              step="0.01"
              value={form.monthly_fee}
              onChange={(e) => setForm((f) => ({ ...f, monthly_fee: e.target.value }))}
            />
          </div>
          <Input
            label={t("orgs.subscriptionExpiry")}
            type="date"
            value={form.subscription_expiry}
            onChange={(e) => setForm((f) => ({ ...f, subscription_expiry: e.target.value }))}
          />
          {saveError && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {saveError}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setEditOpen(false)}
              className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
            >
              {t("common.cancel")}
            </button>
            <Button type="submit" loading={saving} className="w-auto px-6">
              {t("common.save")}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 capitalize text-slate-200">{value}</div>
    </div>
  );
}

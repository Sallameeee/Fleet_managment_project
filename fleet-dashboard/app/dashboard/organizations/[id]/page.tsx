"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  getOrganization,
  setOrganizationStatus,
  type OrganizationDetail,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import OrgEditForm from "@/components/OrgEditForm";
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

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="break-words text-2xl font-semibold text-white">{org.name}</h1>
            <StatusBadge status={org.status} />
          </div>
          <p className="text-sm text-slate-500">{org.slug}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setEditOpen(true)}
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
      <div className="grid grid-cols-2 gap-4 rounded-xl border border-ink-800 bg-ink-900/40 p-4 text-sm sm:p-5 md:grid-cols-4">
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
        <div className="overflow-x-auto rounded-xl border border-ink-800">
          <table className="w-full min-w-[520px] text-left text-sm">
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
        <div className="overflow-x-auto rounded-xl border border-ink-800">
          <table className="w-full min-w-[520px] text-left text-sm">
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

      {/* Edit — shared sectioned, scrollable form (same as the list page). */}
      {editOpen && (
        <OrgEditForm
          org={org}
          onClose={() => setEditOpen(false)}
          onSaved={() => {
            setEditOpen(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 break-words capitalize text-slate-200">{value}</div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listChangeRequests,
  decideChangeRequest,
  CapacityExceededError,
  type ManagerChangeRequest,
  type ChangeRequestBus,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import Modal from "@/components/Modal";

type Filter = "pending" | "approved" | "rejected" | "all";
const FILTERS: Filter[] = ["pending", "approved", "rejected", "all"];

export default function ManagerChangeRequestsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();

  const [filter, setFilter] = useState<Filter>("pending"); // pending first by default
  const [rows, setRows] = useState<ManagerChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);
  // The would-exceed confirmation (force override).
  const [forcing, setForcing] = useState<{ req: ManagerChangeRequest; message: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listChangeRequests(filter === "all" ? undefined : filter));
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

  async function decide(req: ManagerChangeRequest, action: "approve" | "reject", force = false) {
    setActioningId(req.id);
    try {
      await decideChangeRequest(req.id, action, force);
      toast.success(action === "approve" ? t("cr.approved") : t("cr.rejected"));
      setForcing(null);
      await load();
    } catch (e) {
      if (e instanceof CapacityExceededError) {
        // Soft block: let the manager override with the numbers in hand.
        setForcing({ req, message: e.message });
      } else {
        toast.error(e instanceof Error ? e.message : t("common.failed"));
      }
    } finally {
      setActioningId(null);
    }
  }

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-white">{t("cr.title")}</h1>
        <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("cr.subtitle")}</p>
      </div>

      {/* Status filter */}
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
            {f === "all" ? t("cr.filterAll") : f === "pending" ? t("cr.statusPending") : f === "approved" ? t("cr.statusApproved") : t("cr.statusRejected")}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      {!loading && rows.length === 0 && !error && (
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("cr.none")}</div>
      )}

      <div className="space-y-4">
        {rows.map((r) => (
          <RequestCard key={r.id} r={r} actioning={actioningId === r.id} onApprove={() => decide(r, "approve")} onReject={() => decide(r, "reject")} />
        ))}
      </div>

      {/* Force-override confirmation when the requested bus would exceed capacity. */}
      <Modal open={forcing !== null} onClose={() => setForcing(null)} title={t("cr.forceTitle")}>
        {forcing && (
          <div className="space-y-4">
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {forcing.message}
            </div>
            <p className="text-sm text-slate-300">
              {forcing.req.student_name ?? "—"} · {forcing.req.requested_bus?.route_name ?? "—"}
              {forcing.req.requested_bus?.vehicle_bus_number ? ` · ${forcing.req.requested_bus.vehicle_bus_number}` : ""}
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setForcing(null)}
                className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
              >
                {t("cr.forceCancel")}
              </button>
              <button
                type="button"
                disabled={actioningId === forcing.req.id}
                onClick={() => decide(forcing.req, "approve", true)}
                className="rounded-lg bg-amber-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-500 disabled:opacity-50"
              >
                {t("cr.forceConfirm")}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function RequestCard({
  r,
  actioning,
  onApprove,
  onReject,
}: {
  r: ManagerChangeRequest;
  actioning: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const { t } = useT();
  const pending = r.status === "pending";

  const dateLabel = (() => {
    const d = new Date(r.request_date);
    return Number.isNaN(d.getTime()) ? r.request_date : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  })();

  const statusChip = {
    approved: { cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", label: t("cr.statusApproved") },
    rejected: { cls: "border-red-500/40 bg-red-500/10 text-red-300", label: t("cr.statusRejected") },
    pending: { cls: "border-amber-500/40 bg-amber-500/10 text-amber-300", label: t("cr.statusPending") },
  }[r.status];

  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">{r.student_name ?? "—"}</h2>
          <p className="text-sm text-slate-400">
            {t("cr.requestedBy")} <span className="text-slate-300">{r.parent_email ?? "—"}</span>
          </p>
          <p className="mt-1 text-sm text-slate-400">
            {t("cr.forDate")} <span className="text-slate-200">{dateLabel}</span>
            {r.requested_stop ? (
              <>
                {" · "}
                {t("cr.dropOff")}: <span className="text-slate-200">{r.requested_stop}</span>
              </>
            ) : null}
          </p>
        </div>
        <span className={"rounded-full border px-3 py-1 text-xs font-semibold " + statusChip.cls}>{statusChip.label}</span>
      </div>

      {/* Before/after capacity on both buses */}
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <BusPanel title={t("cr.currentBus")} bus={r.current_bus} tone="leaving" />
        <BusPanel title={t("cr.requestedBus")} bus={r.requested_bus} tone="joining" />
      </div>

      {pending && (
        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onReject}
            disabled={actioning}
            className="rounded-lg border border-red-500/40 px-4 py-2 text-sm font-medium text-red-300 transition-colors hover:bg-red-500/10 disabled:opacity-50"
          >
            {t("cr.reject")}
          </button>
          <button
            onClick={onApprove}
            disabled={actioning}
            className="rounded-lg bg-brand px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage disabled:opacity-50"
          >
            {t("cr.approve")}
          </button>
        </div>
      )}
    </div>
  );
}

function BusPanel({ title, bus, tone }: { title: string; bus: ChangeRequestBus | null; tone: "leaving" | "joining" }) {
  const { t } = useT();
  if (!bus) return null;
  const arrowColor = tone === "joining" ? "text-brand-sage" : "text-slate-400";
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-white">{bus.route_name ?? "—"}</span>
        {bus.vehicle_bus_number ? <span className="text-xs text-slate-400">🚌 {bus.vehicle_bus_number}</span> : null}
      </div>
      <p className="mt-0.5 text-xs uppercase tracking-wide text-slate-500">{title}</p>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="text-2xl font-bold text-white">{bus.count_now}</span>
        <span className={"text-sm " + arrowColor}>→</span>
        <span className="text-2xl font-bold text-white">{bus.count_after}</span>
        <span className="text-xs text-slate-500">
          {t("cr.now")} → {t("cr.after")}
        </span>
      </div>

      <div className="mt-1.5 text-xs">
        {bus.capacity === null ? (
          <span className="text-slate-500">{t("cr.noCapacity")}</span>
        ) : bus.would_exceed ? (
          <span className="inline-flex items-center gap-1 rounded-md border border-red-500/40 bg-red-500/10 px-2 py-0.5 font-semibold text-red-300">
            ⚠ {t("cr.full")} ({bus.count_after}/{bus.capacity})
          </span>
        ) : (
          <span className="text-slate-400">
            {bus.capacity} {t("cr.capacity")} ·{" "}
            <span className={bus.seats_free_after !== null && bus.seats_free_after <= 2 ? "text-amber-300" : "text-emerald-300"}>
              {bus.seats_free_after} {t("cr.seatsFree")}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

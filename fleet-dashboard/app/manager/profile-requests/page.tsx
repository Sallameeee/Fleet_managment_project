"use client";

import { useCallback, useEffect, useState } from "react";
import {
  listProfileRequests,
  decideProfileRequest,
  type ManagerProfileRequest,
  type ProfileFields,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import { useFocusHighlight } from "@/lib/useFocusHighlight";

type Filter = "pending" | "approved" | "rejected" | "all";
const FILTERS: Filter[] = ["pending", "approved", "rejected", "all"];

export default function ManagerProfileRequestsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();

  const [filter, setFilter] = useState<Filter>("pending");
  const [rows, setRows] = useState<ManagerProfileRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await listProfileRequests(filter === "all" ? undefined : filter));
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

  // Jump to a specific request when arriving from a notification (?focus=<id>).
  const { focus, highlight } = useFocusHighlight("pr-", !loading);
  useEffect(() => {
    if (focus) setFilter("all");
  }, [focus]);

  async function decide(r: ManagerProfileRequest, action: "approve" | "reject") {
    setActioningId(r.id);
    try {
      await decideProfileRequest(r.id, action);
      toast.success(action === "approve" ? t("pr.approved") : t("pr.rejected"));
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("common.failed"));
    } finally {
      setActioningId(null);
    }
  }

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-6 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("pr.title")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : t("pr.subtitle")}</p>
        </div>
        <button
          onClick={load}
          className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white"
        >
          ↻ {t("common.reload")}
        </button>
      </div>

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
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("pr.none")}</div>
      )}

      <div className="space-y-4">
        {rows.map((r) => (
          <div key={r.id} id={"pr-" + r.id} className={highlight === r.id ? "rounded-xl ring-2 ring-brand ring-offset-2 ring-offset-ink-950" : ""}>
            <ProfileRequestCard r={r} actioning={actioningId === r.id} onApprove={() => decide(r, "approve")} onReject={() => decide(r, "reject")} />
          </div>
        ))}
      </div>
    </div>
  );
}

function ProfileRequestCard({
  r,
  actioning,
  onApprove,
  onReject,
}: {
  r: ManagerProfileRequest;
  actioning: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const { t } = useT();
  const pending = r.status === "pending";

  const statusChip = {
    approved: { cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300", label: t("cr.statusApproved") },
    rejected: { cls: "border-red-500/40 bg-red-500/10 text-red-300", label: t("cr.statusRejected") },
    pending: { cls: "border-amber-500/40 bg-amber-500/10 text-amber-300", label: t("cr.statusPending") },
  }[r.status];

  // Only the fields the parent actually asked to change (proposed != null).
  const changes = (
    [
      { key: "name", label: t("pr.field.name") },
      { key: "phone", label: t("pr.field.phone") },
      { key: "email", label: t("pr.field.email") },
    ] as { key: keyof ProfileFields; label: string }[]
  ).filter((f) => r.proposed[f.key] != null && r.proposed[f.key] !== "");

  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold text-white">{r.current.name ?? r.current.email ?? "—"}</h2>
        <span className={"rounded-full border px-3 py-1 text-xs font-semibold " + statusChip.cls}>{statusChip.label}</span>
      </div>

      <div className="mt-3 space-y-2">
        {changes.map((f) => (
          <div key={f.key} className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm">
            <div className="text-xs uppercase tracking-wide text-slate-500">{f.label}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="text-slate-400 line-through">{r.current[f.key] ?? "—"}</span>
              <span className="text-brand-sage">→</span>
              <span className="font-semibold text-white">{r.proposed[f.key]}</span>
            </div>
          </div>
        ))}
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

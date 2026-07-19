"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { listParents, type ManagerParent, type ParentChild } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";

export default function ManagerParentsPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const [parents, setParents] = useState<ManagerParent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setParents(await listParents());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (isSchool) load();
  }, [isSchool, load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return parents;
    return parents.filter(
      (p) =>
        (p.name ?? "").toLowerCase().includes(q) ||
        (p.email ?? "").toLowerCase().includes(q) ||
        p.children.some((c) => (c.name ?? "").toLowerCase().includes(q)),
    );
  }, [parents, query]);

  if (!isSchool) {
    return <div className="text-sm text-slate-400">This page is only available for school organizations.</div>;
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{t("parents.title")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${parents.length} · ${t("parents.subtitle")}`}</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("parents.search")}
            className="w-56 max-w-[60vw] rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-slate-100 focus:border-brand focus:outline-none"
          />
          <button onClick={load} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white">
            ↻ {t("common.reload")}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button>
        </div>
      )}

      {!loading && parents.length === 0 && !error && (
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("parents.none")}</div>
      )}
      {!loading && parents.length > 0 && filtered.length === 0 && (
        <div className="rounded-xl border border-ink-800 px-4 py-12 text-center text-slate-500">{t("parents.noMatch")}</div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {filtered.map((p) => (
          <ParentCard key={p.id} p={p} />
        ))}
      </div>
    </div>
  );
}

function ParentCard({ p }: { p: ManagerParent }) {
  const { t } = useT();
  const initials = (p.name ?? p.email ?? "?").trim().charAt(0).toUpperCase() || "?";
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
      {/* Parent header */}
      <div className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-brand/15 text-sm font-bold text-brand-sage">{initials}</div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-lg font-semibold text-white">{p.name ?? "—"}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-sm text-slate-400">
            {p.email && (
              <a href={`mailto:${p.email}`} className="truncate hover:text-brand-sage">✉ {p.email}</a>
            )}
            {p.phone && (
              <a href={`tel:${p.phone}`} className="hover:text-brand-sage">📞 {p.phone}</a>
            )}
          </div>
        </div>
        <span className="shrink-0 rounded-full border border-ink-700 px-2.5 py-1 text-xs font-semibold text-slate-300">
          {p.children.length} · {t("parents.children")}
        </span>
      </div>

      {/* Children */}
      <div className="mt-4 space-y-2">
        {p.children.length === 0 ? (
          <div className="text-sm text-slate-500">{t("parents.noChildren")}</div>
        ) : (
          p.children.map((c) => <ChildRow key={c.id} c={c} />)
        )}
      </div>
    </div>
  );
}

function ChildRow({ c }: { c: ParentChild }) {
  const { t } = useT();
  const meta = [c.grade, c.class_name].filter(Boolean).join(" · ");
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="font-medium text-white">{c.name ?? "—"}</span>
        {meta && <span className="text-xs text-slate-400">{meta}</span>}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-slate-400">
        <span className="inline-flex items-center gap-1">
          🚌 {c.route_name ?? <span className="text-slate-600">{t("parents.noRoute")}</span>}
        </span>
        <span className="text-slate-600">·</span>
        <span className="inline-flex items-center gap-1">
          🏁 {c.drop_off_stop ?? <span className="text-slate-600">{t("parents.noDropOff")}</span>}
        </span>
      </div>
    </div>
  );
}

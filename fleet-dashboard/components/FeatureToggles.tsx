"use client";

import { useEffect, useState } from "react";
import { getFeatureCatalog, type FeatureCatalog } from "@/lib/api";

/** Super-admin feature toggles for an org, strictly scoped to the chosen module
 * (school/university feature sets never mix). Core features are shown LOCKED ON;
 * toggleable ones are checkboxes. `value` is the enabled toggleable-key array. */
export default function FeatureToggles({
  module,
  value,
  onChange,
}: {
  module: "university" | "school";
  // null = LEGACY org (enabled_features unset) → treat as ALL toggleable on, so
  // opening an existing org's editor never shows features as off / disables them.
  value: string[] | null;
  onChange: (keys: string[]) => void;
}) {
  const [cat, setCat] = useState<FeatureCatalog | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setCat(null);
    setErr(null);
    getFeatureCatalog(module)
      .then((c) => active && setCat(c))
      .catch((e) => active && setErr(e instanceof Error ? e.message : "Failed to load features."));
    return () => {
      active = false;
    };
  }, [module]);

  if (err) return <div className="text-sm text-red-300">{err}</div>;
  if (!cat) return <div className="text-sm text-slate-500">Loading features…</div>;

  const allKeys = cat.toggleable.map((f) => f.key);
  const effective = value ?? allKeys; // legacy (null) → everything on

  function toggle(key: string, on: boolean) {
    const set = new Set(value ?? allKeys); // materialize legacy all-on on first edit
    if (on) set.add(key);
    else set.delete(key);
    onChange([...set]);
  }

  return (
    <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900/40 p-3">
      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Core — always on</div>
        <div className="flex flex-wrap gap-1.5">
          {cat.core.map((f) => (
            <span key={f.key} className="inline-flex items-center gap-1 rounded-full border border-ink-700 bg-ink-850 px-2.5 py-1 text-xs text-slate-400">
              <span aria-hidden>🔒</span> {f.label}
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Enabled features</div>
        {cat.toggleable.length === 0 ? (
          <div className="text-xs text-slate-500">No optional features for this module.</div>
        ) : (
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {cat.toggleable.map((f) => {
              const on = effective.includes(f.key);
              return (
                <label
                  key={f.key}
                  className={
                    "flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors " +
                    (on ? "border-brand/50 bg-brand/10 text-white" : "border-ink-700 text-slate-300 hover:border-ink-600")
                  }
                >
                  <input type="checkbox" checked={on} onChange={(e) => toggle(f.key, e.target.checked)} className="h-4 w-4 accent-[#3AA76D]" />
                  {f.label}
                </label>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

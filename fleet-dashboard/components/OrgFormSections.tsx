"use client";

import type { ReactNode } from "react";
import { useT } from "@/lib/i18n";
import FeatureToggles from "@/components/FeatureToggles";

/** A titled card that visually separates one section of the org form. */
export function FormSection({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>
      {hint && <p className="mt-0.5 text-xs text-slate-500">{hint}</p>}
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

/**
 * The MODULE selector + the FEATURES toggles, shared by the create and edit
 * forms. Kept together because the feature catalog is strictly module-scoped —
 * changing the module resets the feature selection to that module's core-only
 * default (as designed), which we make VISIBLE here so it isn't surprising.
 *
 * `features` is null for a LEGACY org (enabled_features unset) → FeatureToggles
 * renders it as all-on and only materializes on a real edit, so opening/saving
 * without touching toggles never wipes a legacy org.
 */
export function ModuleFeaturesSection({
  module,
  features,
  originalModule,
  onModuleChange,
  onFeaturesChange,
}: {
  module: "university" | "school";
  features: string[] | null;
  /** The module the org had before editing — used to warn when it's being changed. */
  originalModule?: "university" | "school";
  onModuleChange: (module: "university" | "school") => void;
  onFeaturesChange: (keys: string[]) => void;
}) {
  const { t } = useT();
  const moduleChanged = originalModule != null && module !== originalModule;
  const selectCls =
    "w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40";

  return (
    <>
      <FormSection title={t("orgs.module")}>
        <label className="block">
          <select
            value={module}
            // Switching the module RESETS the feature selection (core-only for the
            // new module). onModuleChange handles that in the parent.
            onChange={(e) => onModuleChange(e.target.value as "university" | "school")}
            className={selectCls}
          >
            <option value="university">{t("orgs.moduleUniversity")}</option>
            <option value="school">{t("orgs.moduleSchool")}</option>
          </select>
        </label>
        {moduleChanged ? (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            ⚠ {t("orgs.moduleChangedReset")}
          </div>
        ) : (
          <p className="text-xs text-slate-500">{t("orgs.moduleResetHint")}</p>
        )}
      </FormSection>

      <FormSection title={t("orgs.features")} hint={t("orgs.featuresHint")}>
        <FeatureToggles module={module} value={features} onChange={onFeaturesChange} />
      </FormSection>
    </>
  );
}

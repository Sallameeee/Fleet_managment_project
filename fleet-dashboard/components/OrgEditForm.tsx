"use client";

import { useState } from "react";
import { updateOrganization, type Organization, type OrgPatch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import { FormSection, ModuleFeaturesSection } from "@/components/OrgFormSections";

const FORM_ID = "org-edit-form";

/**
 * Self-contained EDIT modal for an organization's subscription + module +
 * features. Reused by the organizations LIST (per-row Edit) and the org DETAIL
 * page, so both open the same sectioned, scrollable form.
 *
 * Correctness safeguards (kept from the original detail-page form):
 *  • A LEGACY org (enabled_features == null) starts with features = null →
 *    FeatureToggles shows all-on, and we only send enabled_features in the patch
 *    when it's non-null. So opening/saving without touching toggles NEVER wipes
 *    a legacy org to core-only.
 *  • Changing the module resets features to [] (the backend then applies the new
 *    module's core-only default) — surfaced visibly via ModuleFeaturesSection.
 */
export default function OrgEditForm({
  org,
  onClose,
  onSaved,
}: {
  org: Organization;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useT();
  const originalModule = ((org.module as string) ?? "university") as "university" | "school";
  const [plan, setPlan] = useState((org.plan as string) ?? "basic");
  const [module, setModule] = useState<"university" | "school">(originalModule);
  const [features, setFeatures] = useState<string[] | null>(org.enabled_features ?? null);
  const [maxDevices, setMaxDevices] = useState(String(org.max_devices ?? ""));
  const [monthlyFee, setMonthlyFee] = useState(String(org.monthly_fee ?? ""));
  const [expiry, setExpiry] = useState(org.subscription_expiry ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const patch: OrgPatch = {
        plan: plan as OrgPatch["plan"],
        module: module as OrgPatch["module"],
        max_devices: Number(maxDevices) || 0,
        monthly_fee: Number(monthlyFee) || 0,
        subscription_expiry: expiry || null,
      };
      // Only send features when the admin actually materialized/changed them.
      // null = untouched legacy org → leave as-is (unless the module changed, which
      // the backend resets on its own).
      if (features !== null) patch.enabled_features = features;
      await updateOrganization(org.id, patch);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  const selectCls =
    "w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40";

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={`${t("orgsd.editSubscription")} — ${org.name}`}
      footer={
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white"
          >
            {t("common.cancel")}
          </button>
          <Button type="submit" form={FORM_ID} loading={saving} className="w-auto px-6">
            {t("common.save")}
          </Button>
        </div>
      }
    >
      <form id={FORM_ID} onSubmit={handleSave} className="space-y-4">
        <FormSection title={t("orgs.subscriptionSection")}>
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("orgs.plan")}</span>
            <select value={plan} onChange={(e) => setPlan(e.target.value)} className={selectCls}>
              <option value="basic">basic</option>
              <option value="pro">pro</option>
              <option value="enterprise">enterprise</option>
            </select>
          </label>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label={t("common.maxDevices")} type="number" min={0} value={maxDevices} onChange={(e) => setMaxDevices(e.target.value)} />
            <Input label={t("orgs.monthlyFee")} type="number" min={0} step="0.01" value={monthlyFee} onChange={(e) => setMonthlyFee(e.target.value)} />
          </div>
          <Input label={t("orgs.subscriptionExpiry")} type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </FormSection>

        <ModuleFeaturesSection
          module={module}
          features={features}
          originalModule={originalModule}
          onModuleChange={(m) => {
            setModule(m);
            setFeatures([]); // reset to core-only for the new module (backend enforces too)
          }}
          onFeaturesChange={setFeatures}
        />

        {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>}
      </form>
    </Modal>
  );
}

"use client";

import { useT } from "@/lib/i18n";

// EN / العربية toggle. Persisted + RTL handled by the provider.
export default function LanguageSwitcher() {
  const { lang, setLang } = useT();
  return (
    <button
      onClick={() => setLang(lang === "ar" ? "en" : "ar")}
      title="Language / اللغة"
      className="rounded-lg border border-ink-700 px-2.5 py-1.5 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white"
    >
      {lang === "ar" ? "EN" : "ع"}
    </button>
  );
}

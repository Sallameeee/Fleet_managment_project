"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { managerLogin } from "@/lib/manager";
import { useT } from "@/lib/i18n";
import Button from "@/components/Button";
import Input from "@/components/Input";

export default function OrgLoginPage() {
  const router = useRouter();
  const { t } = useT();
  const [username, setUsername] = useState("");
  const [slug, setSlug] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await managerLogin(username, slug, password);
      router.push("/manager");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-brand text-xl font-bold text-white">
            F
          </div>
          <h1 className="text-2xl font-semibold text-white">{t("login.managerTitle")}</h1>
          <p className="mt-1 text-sm text-slate-400">{t("login.managerSubtitle")}</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-2xl border border-ink-800 bg-ink-900/70 p-6 shadow-xl"
        >
          <Input
            id="username"
            label={t("common.username")}
            type="text"
            placeholder="e.g. acme_admins"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <Input
            id="slug"
            label={t("common.organization")}
            type="text"
            placeholder="e.g. acme-bus-co"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            required
          />
          <Input
            id="password"
            label={t("common.password")}
            type="password"
            placeholder="••••••••"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300"
            >
              {error}
            </div>
          )}

          <Button type="submit" loading={loading}>
            {t("login.signIn")}
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-slate-500">
          {t("login.orgHint")}
        </p>
      </div>
    </main>
  );
}

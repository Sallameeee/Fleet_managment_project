"use client";

import { useEffect, useState, type FormEvent } from "react";
import Modal from "@/components/Modal";
import Input from "@/components/Input";
import Button from "@/components/Button";
import { useT } from "@/lib/i18n";
import { managerLogin } from "@/lib/manager";
import { login as adminLogin } from "@/lib/api";

type Props = {
  kind: "manager" | "admin";
  open: boolean;
  /** Username (manager) or email (admin) to prefill, when known. */
  identity?: string | null;
  /** The org slug of the manager session. */
  slug?: string | null;
  /** The signed-in user's id: a re-login must be the SAME account. */
  expectedId?: string | null;
  onDone: () => void;
  onLater: () => void;
};

/** Asks for the password when the server no longer knows the session. The page
 *  underneath stays mounted, so nothing on it is lost. */
export default function ReauthModal({ kind, open, identity, slug, expectedId, onDone, onLater }: Props) {
  const { t } = useT();
  const [who, setWho] = useState(identity ?? "");
  const [org, setOrg] = useState(slug ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setWho(identity ?? "");
      setOrg(slug ?? "");
      setPassword("");
      setError(null);
    }
  }, [open, identity, slug]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      const id =
        kind === "manager"
          ? (await managerLogin(who, org, password)).id
          : (await adminLogin(who, password)).admin.id;
      if (expectedId && id && id !== expectedId) {
        setError(t("session.differentAccount"));
        return;
      }
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onLater} title={t("session.reauthTitle")}>
      <form onSubmit={submit} className="space-y-4" data-testid="reauth-modal">
        <p className="text-sm text-slate-400">{t("session.reauthBody")}</p>
        <Input
          label={kind === "manager" ? t("common.username") : t("session.email")}
          value={who}
          onChange={(e) => setWho(e.target.value)}
          autoComplete="username"
          required
        />
        {kind === "manager" && (
          <Input label={t("common.organization")} value={org} onChange={(e) => setOrg(e.target.value)} required />
        )}
        <Input
          label={t("common.password")}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          autoFocus
          required
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex gap-3">
          <button
            type="button"
            onClick={onLater}
            className="w-full rounded-lg border border-ink-700 px-4 py-2.5 text-slate-300 hover:border-brand hover:text-white"
          >
            {t("session.later")}
          </button>
          <Button type="submit" loading={busy}>
            {t("login.signIn")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** A thin strip under the header: offline (retrying) or "needs your password". */
export function SessionStrip({
  state,
  onRetry,
  onSignIn,
}: {
  state: "offline" | "reauth";
  onRetry?: () => void;
  onSignIn?: () => void;
}) {
  const { t } = useT();
  const offline = state === "offline";
  return (
    <div
      role="status"
      data-testid={offline ? "session-offline" : "session-reauth"}
      className={
        "flex items-center justify-between gap-3 border-b px-4 py-2 text-sm md:px-6 " +
        (offline
          ? "border-sky-500/40 bg-sky-500/10 text-sky-100"
          : "border-amber-500/40 bg-amber-500/10 text-amber-100")
      }
    >
      <span>{offline ? t("session.offline") : t("session.expiredStrip")}</span>
      <button
        onClick={offline ? onRetry : onSignIn}
        className="shrink-0 rounded-md border border-current/40 px-3 py-1 font-medium hover:bg-white/10"
      >
        {offline ? t("session.retryNow") : t("login.signIn")}
      </button>
    </div>
  );
}

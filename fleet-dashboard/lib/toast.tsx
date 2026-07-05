"use client";

// App-wide toast/pop-up notifications.
//
// Usage:
//   const toast = useToast();
//   toast.success("Route saved");
//   toast.error(err.message);
//   toast.info("Copied");
// Mounted once via <ToastProvider> in the root layout, so useToast() works
// anywhere in the tree. Toasts float bottom-center, stack, auto-dismiss after a
// few seconds, and are dismissible. RTL + light/dark safe (uses logical props
// and the shared palette).

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

type Variant = "success" | "error" | "info";

interface ToastItemData {
  id: number;
  message: string;
  variant: Variant;
}

interface ToastApi {
  show: (message: string, variant?: Variant) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

const AUTO_DISMISS_MS = 4000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItemData[]>([]);
  const idRef = useRef(0);

  const remove = useCallback((id: number) => {
    setToasts((ts) => ts.filter((x) => x.id !== id));
  }, []);

  const show = useCallback(
    (message: string, variant: Variant = "info") => {
      if (!message) return;
      const id = ++idRef.current;
      setToasts((ts) => [...ts, { id, message, variant }]);
      window.setTimeout(() => remove(id), AUTO_DISMISS_MS);
    },
    [remove],
  );

  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (m) => show(m, "success"),
      error: (m) => show(m, "error"),
      info: (m) => show(m, "info"),
    }),
    [show],
  );

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-6 z-[100] flex flex-col items-center gap-2 px-4">
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onClose={() => remove(t.id)} />
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

function ToastCard({ toast, onClose }: { toast: ToastItemData; onClose: () => void }) {
  const styles: Record<Variant, string> = {
    success: "border-brand/50 bg-brand/15 text-brand-sage",
    error: "border-red-500/50 bg-red-500/15 text-red-300",
    info: "border-ink-700 bg-ink-900/95 text-slate-200",
  };
  const icon: Record<Variant, string> = { success: "✓", error: "✕", info: "ℹ" };
  return (
    <div
      role="status"
      className={
        "pointer-events-auto flex w-full max-w-md items-start gap-2.5 rounded-xl border px-4 py-3 text-sm shadow-2xl backdrop-blur " +
        "animate-[toastIn_.18s_ease-out] " +
        styles[toast.variant]
      }
    >
      <span className="mt-0.5 shrink-0 font-bold" aria-hidden>{icon[toast.variant]}</span>
      <span className="min-w-0 flex-1 break-words">{toast.message}</span>
      <button onClick={onClose} className="shrink-0 text-current opacity-60 hover:opacity-100" aria-label="Dismiss">✕</button>
    </div>
  );
}

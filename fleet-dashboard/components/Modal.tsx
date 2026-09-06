"use client";

import type { ReactNode } from "react";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Optional sticky action bar (e.g. Save/Cancel). Stays pinned while the body
   *  scrolls. A submit button here should use the HTML `form="<id>"` attribute to
   *  submit a form living in `children`. */
  footer?: ReactNode;
  /** Widen the modal for form-heavy content. Default max-w-lg. */
  size?: "md" | "lg";
};

export default function Modal({ open, onClose, title, children, footer, size = "md" }: ModalProps) {
  if (!open) return null;
  const maxW = size === "lg" ? "max-w-2xl" : "max-w-lg";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden />
      {/* Flex column capped to the viewport: header + footer stay put, body scrolls.
          Works on small screens (max-h clamps to the viewport, p-4 keeps a margin). */}
      <div className={`relative z-10 flex max-h-[calc(100dvh-2rem)] w-full ${maxW} flex-col rounded-2xl border border-ink-800 bg-ink-900 shadow-2xl`}>
        <div className="flex shrink-0 items-center justify-between border-b border-ink-800 px-6 py-4">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            onClick={onClose}
            className="text-slate-400 transition-colors hover:text-white"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
        {footer && <div className="shrink-0 border-t border-ink-800 px-6 py-3">{footer}</div>}
      </div>
    </div>
  );
}

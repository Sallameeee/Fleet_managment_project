"use client";

import { useEffect, useState } from "react";

/**
 * Reads a `?focus=<id>` query param (client-only — no Suspense boundary needed,
 * unlike next/navigation's useSearchParams), then once `ready` scrolls to the
 * element `#${prefix}${id}` and briefly highlights it. Used to jump from a
 * notification straight to the request it refers to.
 *
 * Returns `focus` (the raw id, e.g. to widen a status filter so the item shows)
 * and `highlight` (the id to ring for a moment, or null).
 */
export function useFocusHighlight(prefix: string, ready: boolean): { focus: string | null; highlight: string | null } {
  const [focus, setFocus] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  useEffect(() => {
    setFocus(new URLSearchParams(window.location.search).get("focus"));
  }, []);

  useEffect(() => {
    if (!focus || !ready) return;
    const el = document.getElementById(prefix + focus);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlight(focus);
    const id = setTimeout(() => setHighlight(null), 2600);
    return () => clearTimeout(id);
  }, [focus, prefix, ready]);

  return { focus, highlight };
}

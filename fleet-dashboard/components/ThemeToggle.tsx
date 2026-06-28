"use client";

import { useEffect, useState } from "react";

// Global theme toggle: flips the `light` class on <html>, persisted in
// localStorage. Default is dark. Applies to the WHOLE app (manager + super-admin).
export default function ThemeToggle() {
  const [light, setLight] = useState(false);

  useEffect(() => {
    setLight(document.documentElement.classList.contains("light"));
  }, []);

  function toggle() {
    const next = !light;
    setLight(next);
    document.documentElement.classList.toggle("light", next);
    try {
      localStorage.setItem("fleet_theme", next ? "light" : "dark");
    } catch {
      /* storage blocked; theme still applies for this session */
    }
  }

  return (
    <button
      onClick={toggle}
      title="Toggle light / dark"
      className="rounded-lg border border-ink-700 px-2.5 py-1.5 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white"
    >
      {light ? "☀ Light" : "🌙 Dark"}
    </button>
  );
}

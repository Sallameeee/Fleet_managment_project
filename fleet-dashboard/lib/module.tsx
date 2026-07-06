"use client";

import { createContext, useContext } from "react";

/**
 * The current org's feature module ('university' | 'school'), provided by the
 * manager layout from GET /auth/me. Drives lightweight, module-gated relabels
 * (e.g. "Drivers" → "Supervisors" in school orgs). University stays the default.
 */
const ModuleContext = createContext<string>("university");

export function ModuleProvider({ module, children }: { module: string; children: React.ReactNode }) {
  return <ModuleContext.Provider value={module}>{children}</ModuleContext.Provider>;
}

export function useModule(): string {
  return useContext(ModuleContext);
}

export function useIsSchool(): boolean {
  return useContext(ModuleContext) === "school";
}

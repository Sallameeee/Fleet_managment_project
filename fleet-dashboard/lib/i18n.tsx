"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { dict, type Lang } from "./dict";

type Dir = "ltr" | "rtl";

interface I18nCtx {
  lang: Lang;
  dir: Dir;
  setLang: (l: Lang) => void;
  t: (key: string) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");

  // Hydrate from localStorage on mount (default English).
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("fleet_lang");
      if (saved === "ar" || saved === "en") setLangState(saved);
    } catch {
      /* ignore */
    }
  }, []);

  // Reflect language + direction on <html> whenever it changes.
  useEffect(() => {
    const dir: Dir = lang === "ar" ? "rtl" : "ltr";
    document.documentElement.lang = lang;
    document.documentElement.dir = dir;
  }, [lang]);

  function setLang(l: Lang) {
    setLangState(l);
    try {
      window.localStorage.setItem("fleet_lang", l);
    } catch {
      /* ignore */
    }
  }

  const dir: Dir = lang === "ar" ? "rtl" : "ltr";
  const t = (key: string) => dict[key]?.[lang] ?? dict[key]?.en ?? key;

  return <Ctx.Provider value={{ lang, dir, setLang, t }}>{children}</Ctx.Provider>;
}

export function useT(): I18nCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useT must be used within <LangProvider>");
  return c;
}

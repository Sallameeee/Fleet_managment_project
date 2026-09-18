"use client";

import { useEffect, useId, useRef, useState } from "react";

/**
 * A select whose dropdown is CONTAINED: it is rendered by us (not the browser's
 * native popup, which Chrome widens to the longest option and lets spill past a
 * narrow modal), sized exactly to the field, clamped to the viewport, and it
 * scrolls when long. Keyboard: Enter/Space/↓ open, ↑/↓ move, Enter picks, Esc
 * closes. Used by the assignment modal (driver / route / vehicle / bus driver)
 * — same behaviour for school and university.
 */
export default function SelectField({
  label,
  value,
  onChange,
  options,
  placeholder = "Select…",
  action,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  /** Optional control rendered at the end of the label row (e.g. "+ Add"). */
  action?: React.ReactNode;
  disabled?: boolean;
}) {
  const id = useId();
  const btnRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Fixed-positioned list so a scrolling modal body never clips it; the list
  // takes exactly the field's width and never runs past the viewport bottom.
  const [pos, setPos] = useState<{ top: number; left: number; width: number; maxH: number } | null>(null);
  const selected = options.find((o) => o.value === value) ?? null;

  function place() {
    const r = btnRef.current?.getBoundingClientRect();
    if (!r) return;
    const below = window.innerHeight - r.bottom - 8;
    const above = r.top - 8;
    const wantH = Math.min(240, Math.max(options.length + 1, 1) * 40 + 8);
    if (below >= Math.min(wantH, 160) || below >= above) {
      setPos({ top: r.bottom + 4, left: r.left, width: r.width, maxH: Math.max(120, Math.min(240, below)) });
    } else {
      const h = Math.max(120, Math.min(240, above));
      setPos({ top: r.top - 4 - h, left: r.left, width: r.width, maxH: h });
    }
  }

  function toggle(next = !open) {
    if (disabled) return;
    if (next) {
      place();
      setActive(Math.max(0, options.findIndex((o) => o.value === value) + 1));
    }
    setOpen(next);
  }

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      const target = e.target as Node;
      if (btnRef.current?.contains(target) || listRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onScroll = (e: Event) => {
      if (listRef.current?.contains(e.target as Node)) return; // scrolling the list itself
      setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("touchstart", close, { passive: true });
    window.addEventListener("resize", onScroll);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("touchstart", close);
      window.removeEventListener("resize", onScroll);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open || active < 0) return;
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  // index 0 = the placeholder ("none") row, 1..n = options
  const rows = [{ value: "", label: placeholder }, ...options];

  function pick(i: number) {
    onChange(rows[i]?.value ?? "");
    setOpen(false);
    btnRef.current?.focus();
  }

  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
        e.preventDefault();
        toggle(true);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(rows.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (active >= 0) pick(active);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className="block">
      <span className="mb-1.5 flex items-center justify-between gap-2 text-sm font-medium text-slate-300">
        <label htmlFor={id}>{label}</label>
        {action}
      </span>
      <button
        id={id}
        ref={btnRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={`${id}-list`}
        disabled={disabled}
        onClick={() => toggle()}
        onKeyDown={onKey}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-start text-slate-100 focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/40 disabled:opacity-60"
      >
        <span className={"min-w-0 flex-1 truncate " + (selected ? "" : "text-slate-500")} title={selected?.label}>
          {selected ? selected.label : placeholder}
        </span>
        <svg className={"h-4 w-4 shrink-0 text-slate-400 transition-transform " + (open ? "rotate-180" : "")} viewBox="0 0 20 20" fill="currentColor" aria-hidden>
          <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.17l3.71-3.94a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
        </svg>
      </button>
      {open && pos && (
        <ul
          id={`${id}-list`}
          ref={listRef}
          role="listbox"
          onKeyDown={onKey}
          style={{ position: "fixed", top: pos.top, left: pos.left, width: pos.width, maxHeight: pos.maxH }}
          className="z-[60] overflow-y-auto overscroll-contain rounded-lg border border-ink-700 bg-ink-900 py-1 shadow-2xl"
        >
          {rows.map((o, i) => {
            const isSel = o.value === value;
            return (
              <li
                key={o.value || "__none"}
                role="option"
                aria-selected={isSel}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(i)}
                title={o.label}
                className={
                  "cursor-pointer truncate px-3 py-2 text-sm " +
                  (i === active ? "bg-brand/20 text-white" : isSel ? "text-brand-sage" : o.value ? "text-slate-200" : "text-slate-500")
                }
              >
                {o.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

"use client";

import { useRef, useState } from "react";
import { useT } from "@/lib/i18n";
import { useToast } from "@/lib/toast";
import type { BulkResult } from "@/lib/manager";

/** One template/CSV column: `key` is sent to the API; `header` is the template
 *  header; `aliases` are other accepted header spellings on import. */
export interface BulkColumn {
  key: string;
  header: string;
  aliases?: string[];
}

// Quote a CSV cell if it contains a comma/quote/newline.
function csvCell(v: string): string {
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

// Minimal CSV line splitter that honors simple double-quoted fields.
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ",") { out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

/** Consistent "Download template" + "Import" pair used across the management
 *  sections. Parses the CSV by header (using each column's aliases), sends the
 *  rows to `onImport`, then reports created/failed + the first few row errors. */
export default function BulkImport({
  templateName,
  columns,
  sample,
  onImport,
  onDone,
}: {
  templateName: string; // e.g. "vehicles_template.csv"
  columns: BulkColumn[];
  sample: Record<string, string>[]; // dummy example rows (keyed by column key)
  onImport: (rows: Record<string, string>[]) => Promise<BulkResult>;
  onDone?: () => void;
}) {
  const { t } = useT();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  function downloadTemplate() {
    const lines = [columns.map((c) => c.header).join(",")];
    for (const row of sample) lines.push(columns.map((c) => csvCell(row[c.key] ?? "")).join(","));
    const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = templateName;
    a.click();
    URL.revokeObjectURL(url);
  }

  function parse(text: string): Record<string, string>[] {
    // Excel prepends a UTF-8 BOM when saving CSV; strip it so the FIRST header
    // (e.g. "name") still matches instead of becoming "﻿name".
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
    if (lines.length < 2) return [];
    const header = splitCsvLine(lines[0]).map((h) => h.replace(/^﻿/, "").trim().toLowerCase());
    // Map each column key to a header index by header or any alias.
    const idxOf = (c: BulkColumn) => {
      const names = [c.header.toLowerCase(), ...(c.aliases ?? []).map((a) => a.toLowerCase())];
      return header.findIndex((h) => names.includes(h));
    };
    const map = columns.map((c) => ({ key: c.key, idx: idxOf(c) }));
    const rows: Record<string, string>[] = [];
    for (let i = 1; i < lines.length; i++) {
      const cells = splitCsvLine(lines[i]);
      const rec: Record<string, string> = {};
      for (const { key, idx } of map) rec[key] = idx >= 0 ? cells[idx] ?? "" : "";
      rows.push(rec);
    }
    return rows;
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) {
      setBusy(true);
      try {
        const rows = parse(await file.text());
        if (rows.length === 0) {
          toast.error(t("bulk.empty"));
        } else {
          const res = await onImport(rows);
          if (res.created > 0) toast.success(`${res.created} ${t("bulk.created")}${res.failed ? ` · ${res.failed} ${t("bulk.failed")}` : ""}`);
          else if (res.failed > 0) toast.error(`${res.failed} ${t("bulk.failed")}`);
          res.errors.slice(0, 6).forEach((er) => toast.error(`${t("bulk.row")} ${er.row}${er.label ? ` (${er.label})` : ""}: ${er.error}`));
          onDone?.();
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("common.failed"));
      } finally {
        setBusy(false);
      }
    }
    if (fileRef.current) fileRef.current.value = ""; // allow re-uploading the same file
  }

  const btn = "rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 transition-colors hover:border-brand hover:text-white disabled:opacity-60";

  return (
    <>
      <button type="button" onClick={downloadTemplate} className={btn}>{t("bulk.template")}</button>
      <button type="button" onClick={() => fileRef.current?.click()} disabled={busy} className={btn}>
        {busy ? t("bulk.importing") : t("bulk.import")}
      </button>
      <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={onFile} className="hidden" />
    </>
  );
}

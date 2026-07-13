"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  listPassengers,
  createPassenger,
  updatePassenger,
  deletePassenger,
  bulkCreatePassengers,
  bulkCreateStudents,
  listRoutes,
  type ManagerPassenger,
  type ManagerRoute,
  type PassengerCreateResult,
  type BulkPassengerRow,
} from "@/lib/manager";
import { useT } from "@/lib/i18n";
import { useIsSchool } from "@/lib/module";
import { useToast } from "@/lib/toast";
import Button from "@/components/Button";
import BulkImport from "@/components/BulkImport";
import Input from "@/components/Input";
import Modal from "@/components/Modal";
import StatusBadge from "@/components/StatusBadge";
import { EditIcon, TrashIcon } from "@/components/RowIcons";

// FRESH student bulk import with the CURRENT fields (parent email = login).
const STUDENT_COLUMNS = [
  { key: "name", header: "name", aliases: ["student name", "student_name"] },
  { key: "parent_phone", header: "parent_phone", aliases: ["parent phone"] },
  { key: "parent_email", header: "parent_email", aliases: ["parent email", "email"] },
  { key: "student_phone", header: "student_phone", aliases: ["student phone"] },
  { key: "grade", header: "grade", aliases: ["year", "school year"] },
  { key: "class_name", header: "class_name", aliases: ["class", "section"] },
  { key: "route", header: "route", aliases: ["route_id", "route name", "route_name"] },
];
const STUDENT_SAMPLE = [
  { name: "Nour Hassan", parent_phone: "0100-123-4567", parent_email: "nour.parent@example.com", student_phone: "", grade: "Grade 5", class_name: "5-B", route: "Maadi Morning" },
  { name: "Youssef Amir", parent_phone: "0111-222-3333", parent_email: "youssef.parent@example.com", student_phone: "0120-999-8888", grade: "Grade 3", class_name: "3-A", route: "Maadi Morning" },
];

function fill(s: string, v: Record<string, string | number>): string {
  return Object.entries(v).reduce((a, [k, val]) => a.split(`{${k}}`).join(String(val)), s);
}

// Very small CSV parser: splits lines/commas and trims. Header row maps columns
// by name so order is flexible. Fields with commas aren't supported (fine for
// this template).
function parseCsv(text: string): BulkPassengerRow[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length < 2) return [];
  const header = lines[0].split(",").map((h) => h.trim().toLowerCase());
  const idx = (names: string[]) => header.findIndex((h) => names.includes(h));
  const iName = idx(["name", "student name", "student_name"]);
  const iUni = idx(["university_id", "university id", "universityid"]);
  const iEmail = idx(["email"]);
  const iRoute = idx(["route", "route_id", "route name", "route_name"]);
  const rows: BulkPassengerRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const c = lines[i].split(",").map((x) => x.trim());
    rows.push({
      name: iName >= 0 ? c[iName] ?? "" : "",
      email: iEmail >= 0 ? c[iEmail] ?? "" : "",
      university_id: iUni >= 0 ? c[iUni] || undefined : undefined,
      route: iRoute >= 0 ? c[iRoute] ?? "" : "",
    });
  }
  return rows;
}

const STUDENT_EMPTY = { name: "", email: "", university_id: "", route_id: "", parent_email: "", parent_phone: "", student_phone: "", grade: "", class_name: "" };

export default function ManagerPassengersPage() {
  const { t } = useT();
  const isSchool = useIsSchool();
  const toast = useToast();
  const [passengers, setPassengers] = useState<ManagerPassenger[]>([]);
  const [routes, setRoutes] = useState<ManagerRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ ...STUDENT_EMPTY });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [created, setCreated] = useState<PassengerCreateResult | null>(null);

  const [editP, setEditP] = useState<ManagerPassenger | null>(null);
  const [eForm, setEForm] = useState({ name: "", university_id: "", route_id: "", is_active: true, parent_email: "", parent_phone: "", student_phone: "", grade: "", class_name: "" });
  const [saving, setSaving] = useState(false);
  const [eError, setEError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPassengers(await listPassengers());
    } catch (e) {
      setError(e instanceof Error ? e.message : t("common.loadFailed"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
    listRoutes().then(setRoutes).catch(() => {});
  }, [load]);

  const routeName = (id: string | null) => routes.find((r) => r.id === id)?.name ?? "—";

  function openCreate() {
    setForm({ ...STUDENT_EMPTY });
    setCreateError(null);
    setCreated(null);
    setOpen(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      // School: the parent's email IS the login (the parent tracks the bus).
      const payload = isSchool
        ? {
            name: form.name.trim(),
            email: form.parent_email.trim(),
            route_id: form.route_id,
            parent_email: form.parent_email.trim(),
            parent_phone: form.parent_phone.trim() || undefined,
            student_phone: form.student_phone.trim() || undefined,
            grade: form.grade.trim() || undefined,
            class_name: form.class_name.trim() || undefined,
          }
        : { name: form.name.trim(), email: form.email.trim(), university_id: form.university_id.trim() || undefined, route_id: form.route_id };
      const res = await createPassenger(payload);
      setCreated(res);
      toast.success(t("toast.created"));
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setCreating(false);
    }
  }

  function openEdit(p: ManagerPassenger) {
    setEditP(p);
    setEForm({
      name: p.name ?? "",
      university_id: p.university_id ?? "",
      route_id: p.route_id ?? "",
      is_active: p.is_active,
      parent_email: p.parent_email ?? "",
      parent_phone: p.parent_phone ?? "",
      student_phone: p.student_phone ?? "",
      grade: p.grade ?? "",
      class_name: p.class_name ?? "",
    });
    setEError(null);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editP) return;
    setSaving(true);
    setEError(null);
    try {
      const patch = isSchool
        ? {
            name: eForm.name.trim(),
            route_id: eForm.route_id,
            is_active: eForm.is_active,
            parent_email: eForm.parent_email.trim() || null,
            parent_phone: eForm.parent_phone.trim() || null,
            student_phone: eForm.student_phone.trim() || null,
            grade: eForm.grade.trim() || null,
            class_name: eForm.class_name.trim() || null,
          }
        : { name: eForm.name.trim(), university_id: eForm.university_id.trim() || null, route_id: eForm.route_id, is_active: eForm.is_active };
      await updatePassenger(editP.id, patch);
      setEditP(null);
      toast.success(t("toast.saved"));
      await load();
    } catch (err) {
      setEError(err instanceof Error ? err.message : t("common.failed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(p: ManagerPassenger) {
    if (!window.confirm(t("pax.deleteConfirm"))) return;
    try {
      await deletePassenger(p.id);
      toast.success(t("toast.deleted"));
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("common.failed"));
    }
  }

  function downloadTemplate() {
    const csv = "name,university_id,email,route\nAhmed Ali,2021001,ahmed@example.com,Downtown Loop\n";
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "passengers_template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) {
      try {
        const text = await file.text();
        const rows = parseCsv(text);
        if (rows.length === 0) {
          toast.error(t("common.failed"));
        } else {
          const res = await bulkCreatePassengers(rows);
          toast.success(fill(t("pax.bulkResult"), { created: res.created, failed: res.failed }));
          res.errors.slice(0, 6).forEach((er) => toast.error(fill(t("pax.rowError"), { row: er.row, email: er.label ?? "", error: er.error })));
          await load();
        }
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("common.failed"));
      }
    }
    if (fileRef.current) fileRef.current.value = ""; // allow re-upload of the same file
  }

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold text-white">{isSchool ? t("nav.students") : t("nav.passengers")}</h1>
          <p className="text-sm text-slate-400">{loading ? t("common.loading") : `${passengers.length} · ${isSchool ? t("students.subtitle") : t("pax.subtitle")}`}</p>
        </div>
        <div className="flex items-center gap-2">
          {isSchool ? (
            // Fresh student bulk import (current fields; parent email = login).
            <BulkImport templateName="students_template.csv" columns={STUDENT_COLUMNS} sample={STUDENT_SAMPLE} onImport={bulkCreateStudents} onDone={load} />
          ) : (
            <>
              <button onClick={downloadTemplate} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("pax.downloadTemplate")}</button>
              <button onClick={() => fileRef.current?.click()} className="rounded-lg border border-ink-700 px-3 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("pax.upload")}</button>
              <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={handleUpload} className="hidden" />
            </>
          )}
          <button onClick={openCreate} className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-sage">+ {isSchool ? t("students.new") : t("pax.newPassenger")}</button>
        </div>
      </div>

      {error && <div className="mb-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{error} <button onClick={load} className="underline hover:text-red-200">{t("common.retry")}</button></div>}

      <div className="overflow-hidden rounded-xl border border-ink-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-900/70 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">{t("pax.studentName")}</th>
              <th className="px-4 py-3">{isSchool ? t("students.class") : t("pax.universityId")}</th>
              <th className="px-4 py-3">{isSchool ? t("students.parentPhone") : t("common.email")}</th>
              <th className="px-4 py-3">{t("pax.route")}</th>
              <th className="px-4 py-3">{t("common.status")}</th>
              <th className="px-4 py-3 text-right">{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-800">
            {!loading && passengers.length === 0 && !error && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">{t("pax.none")}</td></tr>
            )}
            {passengers.map((p) => (
              <tr key={p.id} className="hover:bg-ink-900/40">
                <td className="px-4 py-3 font-medium text-white">{p.name}</td>
                <td className="px-4 py-3 text-slate-400">{isSchool ? (p.class_name ?? "—") : (p.university_id ?? "—")}</td>
                <td className="px-4 py-3 text-slate-400">{isSchool ? (p.parent_phone ?? "—") : p.email}</td>
                <td className="px-4 py-3 text-slate-300">{p.route_name ?? "—"}</td>
                <td className="px-4 py-3"><StatusBadge status={p.is_active ? "active" : "inactive"} /></td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-end gap-1.5">
                    <button onClick={() => openEdit(p)} title={t("pax.editPassenger")} aria-label={t("pax.editPassenger")} className="rounded-md border border-ink-700 p-1.5 text-slate-300 hover:border-brand hover:text-white"><EditIcon /></button>
                    <button onClick={() => handleDelete(p)} title={t("common.delete")} aria-label={t("common.delete")} className="rounded-md border border-red-500/40 p-1.5 text-red-300 hover:bg-red-500/10"><TrashIcon /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Create */}
      <Modal open={open} onClose={() => setOpen(false)} title={isSchool ? t("students.new") : t("pax.newPassenger")}>
        {created ? (
          <div className="space-y-4">
            {created.parent_created === false ? (
              // School: an existing parent was reused (sibling) — no new login.
              <div className="rounded-lg border border-brand/30 bg-brand/10 px-4 py-3 text-sm text-brand-sage">
                <div className="font-semibold text-white">{t("students.linkedExisting")}</div>
                <div className="mt-2 select-all font-mono text-base text-white">{created.email}</div>
              </div>
            ) : (
              <div className="rounded-lg border border-brand/30 bg-brand/10 px-4 py-3 text-sm text-brand-sage">
                <div className="font-semibold text-white">{t("pax.loginTitle")}</div>
                <p className="mt-1 text-xs text-slate-300">{t("pax.loginNote")}</p>
                <div className="mt-2 select-all font-mono text-base text-white">{created.email}</div>
                <div className="mt-1 text-sm">{t("pax.tempPassword")}: <span className="select-all font-mono text-white">{created.default_password}</span></div>
                <div className="mt-1 text-xs text-amber-300">⚠ {t("pax.mustChange")}</div>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button onClick={openCreate} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">+ {t("pax.newPassenger")}</button>
              <Button type="button" onClick={() => setOpen(false)} className="w-auto px-4">{t("common.done")}</Button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleCreate} className="space-y-3">
            <Input label={`${t("pax.studentName")} *`} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
            {isSchool ? (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Input label={`${t("students.parentEmail")} *`} type="email" value={form.parent_email} onChange={(e) => setForm((f) => ({ ...f, parent_email: e.target.value }))} required />
                  <Input label={`${t("students.parentPhone")} *`} value={form.parent_phone} onChange={(e) => setForm((f) => ({ ...f, parent_phone: e.target.value }))} required />
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <Input label={t("students.studentPhone")} value={form.student_phone} onChange={(e) => setForm((f) => ({ ...f, student_phone: e.target.value }))} />
                  <Input label={t("students.grade")} value={form.grade} onChange={(e) => setForm((f) => ({ ...f, grade: e.target.value }))} />
                  <Input label={t("students.class")} value={form.class_name} onChange={(e) => setForm((f) => ({ ...f, class_name: e.target.value }))} />
                </div>
              </>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <Input label={t("pax.universityId")} value={form.university_id} onChange={(e) => setForm((f) => ({ ...f, university_id: e.target.value }))} />
                <Input label={`${t("common.email")} *`} type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} required />
              </div>
            )}
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("pax.route")} *</span>
              <RouteAutocomplete routes={routes} value={form.route_id} onChange={(id) => setForm((f) => ({ ...f, route_id: id }))} />
            </label>
            {createError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{createError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={creating} className="w-auto px-6" disabled={!form.route_id}>{t("common.create")}</Button>
            </div>
          </form>
        )}
      </Modal>

      {/* Edit */}
      <Modal open={editP !== null} onClose={() => setEditP(null)} title={isSchool ? t("students.edit") : t("pax.editPassenger")}>
        {editP && (
          <form onSubmit={handleEdit} className="space-y-3">
            <Input label={`${t("pax.studentName")} *`} value={eForm.name} onChange={(e) => setEForm((f) => ({ ...f, name: e.target.value }))} required />
            {isSchool ? (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <Input label={t("students.parentEmail")} type="email" value={eForm.parent_email} onChange={(e) => setEForm((f) => ({ ...f, parent_email: e.target.value }))} />
                  <Input label={t("students.parentPhone")} value={eForm.parent_phone} onChange={(e) => setEForm((f) => ({ ...f, parent_phone: e.target.value }))} />
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <Input label={t("students.studentPhone")} value={eForm.student_phone} onChange={(e) => setEForm((f) => ({ ...f, student_phone: e.target.value }))} />
                  <Input label={t("students.grade")} value={eForm.grade} onChange={(e) => setEForm((f) => ({ ...f, grade: e.target.value }))} />
                  <Input label={t("students.class")} value={eForm.class_name} onChange={(e) => setEForm((f) => ({ ...f, class_name: e.target.value }))} />
                </div>
              </>
            ) : (
              <Input label={t("pax.universityId")} value={eForm.university_id} onChange={(e) => setEForm((f) => ({ ...f, university_id: e.target.value }))} />
            )}
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium text-slate-300">{t("pax.route")}</span>
              <RouteAutocomplete routes={routes} value={eForm.route_id} onChange={(id) => setEForm((f) => ({ ...f, route_id: id }))} />
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={eForm.is_active} onChange={(e) => setEForm((f) => ({ ...f, is_active: e.target.checked }))} className="h-4 w-4 accent-[#3AA76D]" />
              {t("common.active")}
            </label>
            {eError && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{eError}</div>}
            <div className="flex justify-end gap-2 pt-1">
              <button type="button" onClick={() => setEditP(null)} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:border-brand hover:text-white">{t("common.cancel")}</button>
              <Button type="submit" loading={saving} className="w-auto px-6">{t("common.save")}</Button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}

// Type-ahead route picker: filters the org's existing routes by the typed text.
function RouteAutocomplete({ routes, value, onChange }: { routes: ManagerRoute[]; value: string; onChange: (id: string) => void }) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const selected = routes.find((r) => r.id === value);
  const q = query.trim().toLowerCase();
  const matches = (q ? routes.filter((r) => r.name.toLowerCase().includes(q)) : routes).slice(0, 8);
  return (
    <div className="relative">
      <input
        value={open ? query : selected?.name ?? ""}
        onFocus={() => { setOpen(true); setQuery(selected?.name ?? ""); }}
        onChange={(e) => { setOpen(true); setQuery(e.target.value); }}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        placeholder={t("pax.searchRoute")}
        className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-slate-100 focus:border-brand focus:outline-none"
      />
      {open && matches.length > 0 && (
        <ul className="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-ink-700 bg-ink-900 shadow-xl">
          {matches.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => { onChange(r.id); setQuery(r.name); setOpen(false); }}
                className={"block w-full px-3 py-2 text-start text-sm hover:bg-ink-800 " + (r.id === value ? "text-brand-sage" : "text-slate-200")}
              >
                {r.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

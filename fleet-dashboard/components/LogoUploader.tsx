"use client";

import { useRef, useState } from "react";

import { useT } from "@/lib/i18n";

/** Max logo size (the API enforces the same limit). */
export const LOGO_MAX_BYTES = 1024 * 1024;
const ACCEPT = "image/png,image/jpeg,image/svg+xml,.png,.jpg,.jpeg,.svg";

export function orgInitials(name?: string | null): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "RM";
  return (words.length === 1 ? words[0][0] : words[0][0] + words[1][0]).toUpperCase();
}

/** The org logo in a white rounded chip, or the initials in a green square. */
export function OrgLogoBadge({ url, name, size = 32 }: { url?: string | null; name?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  if (url && !broken) {
    return (
      <span className="inline-flex shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white p-1 ring-1 ring-black/5" style={{ width: size, height: size }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={name ?? "logo"} className="max-h-full max-w-full object-contain" onError={() => setBroken(true)} />
      </span>
    );
  }
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-lg bg-brand font-bold text-white"
      style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }}
    >
      {orgInitials(name)}
    </span>
  );
}

function readAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

/** SVG → PNG (max 512 px) so the apps and PDFs, which only draw raster images, get a crisp logo. */
async function svgToPng(file: File): Promise<string> {
  const svgUrl = await readAsDataUrl(file);
  const img = new Image();
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () => reject(new Error("svg"));
    img.src = svgUrl;
  });
  const w0 = img.naturalWidth || 512, h0 = img.naturalHeight || 512;
  const scale = 512 / Math.max(w0, h0);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(w0 * scale));
  canvas.height = Math.max(1, Math.round(h0 * scale));
  canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/png");
}

/**
 * Upload / replace / remove an organization logo. PNG, JPG or SVG up to 1 MB.
 * An SVG is converted to PNG in the browser before upload (the API also accepts
 * a safe SVG directly).
 */
export default function LogoUploader({
  currentUrl,
  orgName,
  upload,
  remove,
  onChanged,
}: {
  currentUrl?: string | null;
  orgName?: string | null;
  upload: (dataBase64: string, filename: string) => Promise<{ org_logo_url?: string | null }>;
  remove: () => Promise<unknown>;
  onChanged?: (url: string | null) => void;
}) {
  const { t } = useT();
  const input = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState<string | null | undefined>(currentUrl);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function pick(file: File | undefined) {
    setError(null);
    setOk(null);
    if (!file) return;
    const name = file.name.toLowerCase();
    const isSvg = file.type === "image/svg+xml" || name.endsWith(".svg");
    const isRaster = ["image/png", "image/jpeg"].includes(file.type) || /\.(png|jpe?g)$/.test(name);
    if (!isSvg && !isRaster) return setError(t("brand.badType"));
    if (file.size > LOGO_MAX_BYTES) return setError(t("brand.tooBig"));
    setBusy(true);
    try {
      let dataUrl: string;
      try {
        dataUrl = isSvg ? await svgToPng(file) : await readAsDataUrl(file);
      } catch {
        dataUrl = await readAsDataUrl(file); // let the API judge the original
      }
      const res = await upload(dataUrl, file.name);
      setUrl(res.org_logo_url ?? null);
      onChanged?.(res.org_logo_url ?? null);
      setOk(t("brand.saved"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("brand.failed"));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  async function doRemove() {
    setError(null);
    setOk(null);
    setBusy(true);
    try {
      await remove();
      setUrl(null);
      onChanged?.(null);
      setOk(t("brand.removed"));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("brand.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-4">
      <OrgLogoBadge url={url} name={orgName} size={64} />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-xs text-slate-400">{t("brand.help")}</p>
        <div className="flex flex-wrap gap-2">
          <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
          <button
            type="button"
            disabled={busy}
            onClick={() => input.current?.click()}
            className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:bg-brand/90 disabled:opacity-60"
          >
            {busy ? t("common.loading") : url ? t("brand.replace") : t("brand.upload")}
          </button>
          {url && (
            <button type="button" disabled={busy} onClick={doRemove} className="rounded-lg border border-ink-700 px-4 py-2 text-sm text-slate-300 hover:bg-ink-800 disabled:opacity-60">
              {t("brand.remove")}
            </button>
          )}
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
        {ok && <p className="text-sm text-brand-sage">{ok}</p>}
      </div>
    </div>
  );
}

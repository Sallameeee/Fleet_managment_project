"""Organization branding: the org's logo (Supabase Storage) + display name.

Where the logo lives
--------------------
Public Storage bucket `org-logos`, one folder per org:
`org-logos/<org_id>/logo-<epoch_ms>.<png|jpg|svg>`. Every upload gets a NEW
object name (so no CDN or app cache can serve a stale logo), and the older
objects are removed afterwards.

Resilient before migration 047
------------------------------
Migration 047 adds `organizations.logo_url`. Until it is applied, selecting that
column fails; the current logo is then found by LISTING the org's folder (newest
`logo-*` wins). Lookups are cached in-process for 60 s and invalidated on
upload/delete, so login and /auth/me stay fast either way.

Validation (upload)
-------------------
At most 1 MB. The type is sniffed from the bytes, never trusted from the
client: PNG, JPEG, or SVG. An SVG must not carry scripts, event handlers,
`javascript:` URLs, <foreignObject>, entity declarations or external
references (it is served from a public URL).
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
import time
from typing import Optional

from database import SUPABASE_URL, supabase

log = logging.getLogger("branding")

BUCKET = "org-logos"
MAX_BYTES = 1024 * 1024  # 1 MB
_CACHE_TTL_S = 60.0
_cache: dict[str, tuple[float, Optional[str]]] = {}
_cache_lock = threading.Lock()
_bucket_ready = False
_column_state: Optional[bool] = None  # None = unknown, True = logo_url exists, False = missing


class LogoError(ValueError):
    """A rejected upload (message is shown to the user)."""


# ── type sniffing ─────────────────────────────────────────────────────────────
_SVG_FORBIDDEN = [
    (re.compile(rb"<\s*script", re.I), "scripts"),
    (re.compile(rb"\son[a-z]+\s*=", re.I), "event handlers"),
    (re.compile(rb"javascript\s*:", re.I), "javascript: links"),
    (re.compile(rb"<\s*foreignObject", re.I), "embedded HTML"),
    (re.compile(rb"<!\s*ENTITY", re.I), "entity declarations"),
    (re.compile(rb"(?:xlink:)?href\s*=\s*[\"']\s*(?:https?:|//)", re.I), "external links"),
    (re.compile(rb"@import", re.I), "external styles"),
]


def sniff(data: bytes) -> tuple[str, str]:
    """(ext, content_type) for an accepted logo, else LogoError."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or head.startswith(b"<!--"):
        if b"<svg" not in data[:8192].lower():
            raise LogoError("This file is not a PNG, JPG or SVG image.")
        for rx, what in _SVG_FORBIDDEN:
            if rx.search(data):
                raise LogoError(f"This SVG contains {what}, which is not allowed in a logo. Export it as a plain SVG or PNG.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise LogoError("This SVG is not valid UTF-8 text.")
        return "svg", "image/svg+xml"
    raise LogoError("Only PNG, JPG or SVG images are accepted.")


def decode_upload(data_base64: str) -> bytes:
    raw = (data_base64 or "").strip()
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    # A 1 MB file is ~1.37 MB of base64: refuse obviously huge bodies before decoding.
    if len(raw) > (MAX_BYTES * 4) // 3 + 1024:
        raise LogoError("The logo must be 1 MB or smaller.")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise LogoError("The file could not be read (invalid base64).")
    if not data:
        raise LogoError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise LogoError("The logo must be 1 MB or smaller.")
    return data


# ── storage ───────────────────────────────────────────────────────────────────
def public_url(path: str) -> str:
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}"


def _ensure_bucket() -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        supabase.storage.get_bucket(BUCKET)
    except Exception:
        try:
            supabase.storage.create_bucket(
                BUCKET,
                options={
                    "public": True,
                    "file_size_limit": MAX_BYTES,
                    "allowed_mime_types": ["image/png", "image/jpeg", "image/svg+xml"],
                },
            )
        except Exception as e:  # created concurrently / already exists
            if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                raise
    _bucket_ready = True


def _list_logo_objects(org_id: str) -> list[dict]:
    try:
        items = supabase.storage.from_(BUCKET).list(org_id, {"limit": 100, "sortBy": {"column": "name", "order": "desc"}})
    except Exception:
        return []
    return [i for i in (items or []) if str(i.get("name", "")).startswith("logo-")]


def _logo_from_storage(org_id: str) -> Optional[str]:
    objs = _list_logo_objects(org_id)
    if not objs:
        return None
    newest = max(objs, key=lambda o: str(o.get("name")))
    return public_url(f"{org_id}/{newest['name']}")


def _logo_from_column(org_id: str) -> tuple[bool, Optional[str]]:
    """(column_exists, value)."""
    global _column_state
    if _column_state is False:
        return False, None
    try:
        r = supabase.table("organizations").select("logo_url").eq("id", org_id).limit(1).execute().data
        _column_state = True
        return True, (r[0].get("logo_url") if r else None)
    except Exception as e:
        msg = str(e).lower()
        if "logo_url" in msg or "42703" in msg or "pgrst204" in msg or "column" in msg:
            _column_state = False  # migration 047 not applied yet: use the Storage listing
            return False, None
        raise


def logo_url(org_id: Optional[str]) -> Optional[str]:
    """Current public logo URL for an org (cached 60 s), or None. Never raises."""
    if not org_id:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(org_id)
        if hit and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    try:
        has_col, val = _logo_from_column(org_id)
        url = val
        if not has_col or not val:
            url = _logo_from_storage(org_id)
            if has_col and url:  # uploaded before migration 047: heal the column
                try:
                    supabase.table("organizations").update({"logo_url": url}).eq("id", org_id).execute()
                except Exception:
                    pass
    except Exception as e:
        log.warning("logo lookup failed for org %s: %s", org_id, e)
        url = None
    with _cache_lock:
        _cache[org_id] = (now, url)
    return url


def _forget(org_id: str) -> None:
    with _cache_lock:
        _cache.pop(org_id, None)


def _save_column(org_id: str, url: Optional[str]) -> None:
    has_col, _ = _logo_from_column(org_id)
    if has_col:
        supabase.table("organizations").update({"logo_url": url}).eq("id", org_id).execute()


def upload_logo(org_id: str, data: bytes) -> str:
    """Validate + store a new logo for the org; returns its public URL."""
    ext, ctype = sniff(data)
    _ensure_bucket()
    name = f"logo-{int(time.time() * 1000)}.{ext}"
    path = f"{org_id}/{name}"
    supabase.storage.from_(BUCKET).upload(path, data, {"content-type": ctype, "cache-control": "31536000", "upsert": "true"})
    url = public_url(path)
    _save_column(org_id, url)
    # Remove the older logo objects (best-effort).
    try:
        old = [f"{org_id}/{o['name']}" for o in _list_logo_objects(org_id) if o.get("name") != name]
        if old:
            supabase.storage.from_(BUCKET).remove(old)
    except Exception as e:
        log.warning("old logo cleanup failed for org %s: %s", org_id, e)
    _forget(org_id)
    return url


def delete_logo(org_id: str) -> None:
    try:
        objs = [f"{org_id}/{o['name']}" for o in _list_logo_objects(org_id)]
        if objs:
            supabase.storage.from_(BUCKET).remove(objs)
    finally:
        _save_column(org_id, None)
        _forget(org_id)


# ── org display info ──────────────────────────────────────────────────────────
def org_branding(org_id: Optional[str]) -> dict:
    """{org_name, org_logo_url, org_phone} for payloads. Never raises."""
    out = {"org_name": None, "org_logo_url": None, "org_phone": None}
    if not org_id:
        return out
    try:
        r = supabase.table("organizations").select("name, phone").eq("id", org_id).limit(1).execute().data
        if r:
            out["org_name"] = r[0].get("name")
            out["org_phone"] = r[0].get("phone")
    except Exception as e:
        log.warning("org name lookup failed for %s: %s", org_id, e)
    out["org_logo_url"] = logo_url(org_id)
    return out


_img_cache: dict[str, tuple[float, Optional[bytes]]] = {}


def logo_bytes(org_id: Optional[str]) -> tuple[Optional[bytes], Optional[str]]:
    """(raw bytes, ext) of the current logo for PDFs (cached 10 min), or (None, None)."""
    url = logo_url(org_id)
    if not url:
        return None, None
    ext = url.rsplit(".", 1)[-1].lower()
    now = time.monotonic()
    hit = _img_cache.get(url)
    if hit and now - hit[0] < 600:
        return hit[1], ext
    data = None
    try:
        import httpx

        r = httpx.get(url, timeout=6)
        if r.status_code == 200 and len(r.content) <= MAX_BYTES:
            data = r.content
    except Exception as e:
        log.warning("logo download failed (%s): %s", url, e)
    _img_cache[url] = (now, data)
    return data, ext


def pdf_header(org_id: Optional[str], org_name: Optional[str], title: str, styles, width_pt: float = 520):
    """Flowables for a branded PDF header: [logo | org name / title] + a rule.
    PNG/JPG logos are drawn (max 44 pt tall); an SVG or a missing logo falls
    back to the org initials in a green rounded square. Never raises."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

    name = org_name or org_branding(org_id).get("org_name") or "Organization"
    cell = None
    try:
        data, ext = logo_bytes(org_id)
        if data and ext in ("png", "jpg", "jpeg"):
            import io

            from reportlab.lib.utils import ImageReader

            iw, ih = ImageReader(io.BytesIO(data)).getSize()
            h = 44.0
            w = min(140.0, h * (iw / ih if ih else 1))
            h = w * (ih / iw) if iw else h
            cell = Image(io.BytesIO(data), width=w, height=h)
    except Exception as e:
        log.warning("pdf logo failed for org %s: %s", org_id, e)
        cell = None
    if cell is None:
        initials = "".join(p[0] for p in name.split()[:2]).upper() or "•"
        cell = Table([[initials]], colWidths=[40], rowHeights=[40])
        cell.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#3AA76D")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 15),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ]))
    name_style = ParagraphStyle("brand_name", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=colors.HexColor("#1C2B25"))
    sub_style = ParagraphStyle("brand_sub", parent=styles["Normal"], fontSize=10, leading=13, textColor=colors.HexColor("#5B6B64"))
    from xml.sax.saxutils import escape

    text = [Paragraph(escape(name), name_style), Paragraph(escape(title), sub_style)]
    head = Table([[cell, text]], colWidths=[150, width_pt - 150])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#3AA76D")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [head, Spacer(1, 10)]

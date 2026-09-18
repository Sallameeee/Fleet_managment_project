"""Arabic messages for the DETECTION alerts (speeding / off_route / short_stop /
long_stop / offline) — the same bilingual `alerts.meta` mechanism the trip-
lifecycle log points use, extended to the older types. Numbers are written in
Arabic-Indic digits; real place names (route stops) stay in their own language,
like Google Maps does: "توقف ٠ دقيقة عند Orouba Mall، المطلوب ٤ دقائق".

The English `detail` stays the source of truth (it is what the detectors have
always written); this module PARSES it, so the very same rules also run in the
dashboard (lib/alertText.ts) for rows that predate meta. Keep both in sync.
"""

from __future__ import annotations

import re
from typing import Optional

_AR_DIGITS = str.maketrans("0123456789.", "٠١٢٣٤٥٦٧٨٩٫")


def ar_num(s) -> str:
    """'12' -> '١٢', '0.0' -> '٠', '0.2' -> '٠٫٢'."""
    txt = str(s)
    if re.fullmatch(r"-?\d+\.0+", txt):
        txt = txt.split(".")[0]
    return txt.translate(_AR_DIGITS)


def ar_minutes(n_txt: str) -> str:
    """Arabic plural for 'N minutes' given the (possibly decimal) English number."""
    try:
        n = float(n_txt)
    except ValueError:
        return f"{ar_num(n_txt)} دقيقة"
    if n == 1:
        return "دقيقة واحدة"
    if n == 2:
        return "دقيقتين"
    if n != int(n):
        return f"{ar_num(n_txt)} دقيقة"
    if 3 <= n <= 10:
        return f"{ar_num(int(n))} دقائق"
    return f"{ar_num(int(n))} دقيقة"


_SHORT_STOP = re.compile(r"^Stopped (?P<x>[\d.]+) min at (?P<place>.+?), required (?P<n>[\d.]+) min$")
_OFFLINE = re.compile(r"^No GPS data for (?P<g>[\d.]+) min \(limit (?P<l>[\d.]+) min\)$")
_LONG_STOP = re.compile(r"^Long stop: stationary for (?P<m>[\d.]+) min \((?P<where>.+?); limit (?P<l>[\d.]+) min\)$")
_SPEEDING = re.compile(r"^Speed (?P<s>[\d.]+) km/h exceeded limit (?P<t>[\d.]+) km/h(?: \(rule '(?P<rule>.*)'\))?$")
_OFF_ROUTE = re.compile(
    r"^Off route by (?P<d>[\d.]+) m from the (?P<basis>route line|nearest stop) for (?P<s>[\d.]+) s "
    r"\(limit (?P<L>[\d.]+) m / (?P<S>[\d.]+) s(?:, rule '(?P<rule>.*)')?\)$"
)


def alert_message_ar(type_: str, detail: Optional[str]) -> Optional[str]:
    """Arabic rendering of a detector's English detail, or None if unrecognised."""
    if not detail:
        return None
    d = detail.strip()
    if type_ == "short_stop" or d.startswith("Stopped "):
        m = _SHORT_STOP.match(d)
        if m:
            return f"توقف {ar_num(m['x'])} دقيقة عند {m['place']}، المطلوب {ar_minutes(m['n'])}"
    if type_ == "offline" or d.startswith("No GPS"):
        m = _OFFLINE.match(d)
        if m:
            return f"لا توجد بيانات GPS لمدة {ar_minutes(m['g'])} (الحد {ar_minutes(m['l'])})"
    if type_ in ("long_stop", "short_stop") and d.startswith("Long stop"):
        m = _LONG_STOP.match(d)
        if m:
            w = m["where"]
            mw = re.fullmatch(r"([\d.]+) m from the nearest stop", w)
            where_ar = f"على بعد {ar_num(mw.group(1))} م من أقرب محطة" if mw else "على مسار بلا محطات"
            return f"توقف طويل: ثابت لمدة {ar_minutes(m['m'])} ({where_ar}؛ الحد {ar_minutes(m['l'])})"
    if type_ == "speeding":
        m = _SPEEDING.match(d)
        if m:
            rule = f" (قاعدة '{m['rule']}')" if m["rule"] else ""
            return f"السرعة {ar_num(m['s'])} كم/س تجاوزت الحد {ar_num(m['t'])} كم/س{rule}"
    if type_ == "off_route":
        m = re.match(r"^Bus ([\d.]+) m off route, limit ([\d.]+) m$", d)  # legacy wording
        if m:
            return f"الحافلة خارج المسار بمقدار {ar_num(m.group(1))} م، الحد {ar_num(m.group(2))} م"
        m = _OFF_ROUTE.match(d)
        if m:
            basis = "خط المسار" if m["basis"] == "route line" else "أقرب محطة"
            rule = f"، قاعدة '{m['rule']}'" if m["rule"] else ""
            return (f"خرج عن المسار بمقدار {ar_num(m['d'])} م عن {basis} لمدة {ar_num(m['s'])} ث "
                    f"(الحد {ar_num(m['L'])} م / {ar_num(m['S'])} ث{rule})")
    return None


def alert_meta(type_: str, detail: Optional[str]) -> Optional[dict]:
    """alerts.meta for a detection alert: {message_en, message_ar} or None."""
    ar = alert_message_ar(type_, detail)
    return {"message_en": detail, "message_ar": ar} if ar else None

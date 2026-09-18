/**
 * Arabic rendering of the DETECTION alert messages (speeding / off_route /
 * short_stop / long_stop / offline). New rows carry the Arabic in `alerts.meta`
 * (backend alert_text.py); this is the same parser in TS so rows written before
 * that (and any pre-deploy rows) still read Arabic in the Arabic UI. Keep the
 * two implementations in sync. Numbers → Arabic-Indic digits; real place names
 * (route stops) stay in their own language, like Google Maps.
 */

const DIGITS: Record<string, string> = { "0": "٠", "1": "١", "2": "٢", "3": "٣", "4": "٤", "5": "٥", "6": "٦", "7": "٧", "8": "٨", "9": "٩", ".": "٫" };

export function arNum(s: string | number): string {
  let txt = String(s);
  if (/^-?\d+\.0+$/.test(txt)) txt = txt.split(".")[0];
  return txt.replace(/[0-9.]/g, (c) => DIGITS[c] ?? c);
}

function arMinutes(nTxt: string): string {
  const n = Number(nTxt);
  if (Number.isNaN(n)) return `${arNum(nTxt)} دقيقة`;
  if (n === 1) return "دقيقة واحدة";
  if (n === 2) return "دقيقتين";
  if (n !== Math.trunc(n)) return `${arNum(nTxt)} دقيقة`;
  if (n >= 3 && n <= 10) return `${arNum(n)} دقائق`;
  return `${arNum(n)} دقيقة`;
}

const SHORT_STOP = /^Stopped ([\d.]+) min at (.+?), required ([\d.]+) min$/;
const OFFLINE = /^No GPS data for ([\d.]+) min \(limit ([\d.]+) min\)$/;
const LONG_STOP = /^Long stop: stationary for ([\d.]+) min \((.+?); limit ([\d.]+) min\)$/;
const SPEEDING = /^Speed ([\d.]+) km\/h exceeded limit ([\d.]+) km\/h(?: \(rule '(.*)'\))?$/;
const OFF_ROUTE = /^Off route by ([\d.]+) m from the (route line|nearest stop) for ([\d.]+) s \(limit ([\d.]+) m \/ ([\d.]+) s(?:, rule '(.*)')?\)$/;

/** Arabic message for a detection alert's English `detail`, or null if unrecognised. */
export function alertDetailAr(type: string | null | undefined, detail: string | null | undefined): string | null {
  if (!detail) return null;
  const d = detail.trim();
  let m: RegExpMatchArray | null;
  if ((type === "short_stop" || d.startsWith("Stopped ")) && (m = d.match(SHORT_STOP))) {
    return `توقف ${arNum(m[1])} دقيقة عند ${m[2]}، المطلوب ${arMinutes(m[3])}`;
  }
  if ((type === "offline" || d.startsWith("No GPS")) && (m = d.match(OFFLINE))) {
    return `لا توجد بيانات GPS لمدة ${arMinutes(m[1])} (الحد ${arMinutes(m[2])})`;
  }
  if (d.startsWith("Long stop") && (m = d.match(LONG_STOP))) {
    const mw = m[2].match(/^([\d.]+) m from the nearest stop$/);
    const where = mw ? `على بعد ${arNum(mw[1])} م من أقرب محطة` : "على مسار بلا محطات";
    return `توقف طويل: ثابت لمدة ${arMinutes(m[1])} (${where}؛ الحد ${arMinutes(m[3])})`;
  }
  if (type === "speeding" && (m = d.match(SPEEDING))) {
    const rule = m[3] ? ` (قاعدة '${m[3]}')` : "";
    return `السرعة ${arNum(m[1])} كم/س تجاوزت الحد ${arNum(m[2])} كم/س${rule}`;
  }
  if (type === "off_route" && (m = d.match(/^Bus ([\d.]+) m off route, limit ([\d.]+) m$/))) {
    return `الحافلة خارج المسار بمقدار ${arNum(m[1])} م، الحد ${arNum(m[2])} م`; // legacy wording
  }
  if (type === "off_route" && (m = d.match(OFF_ROUTE))) {
    const basis = m[2] === "route line" ? "خط المسار" : "أقرب محطة";
    const rule = m[6] ? `، قاعدة '${m[6]}'` : "";
    return `خرج عن المسار بمقدار ${arNum(m[1])} م عن ${basis} لمدة ${arNum(m[3])} ث (الحد ${arNum(m[4])} م / ${arNum(m[5])} ث${rule})`;
  }
  return null;
}

/** The message to show for an alert/log row in the given UI language. */
export function alertText(lang: string, type: string | null | undefined, detail: string | null | undefined, detailAr?: string | null): string {
  if (lang === "ar") return detailAr || alertDetailAr(type, detail) || detail || "";
  return detail || "";
}

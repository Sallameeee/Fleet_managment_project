// Shared time helpers for the route editor + detail view.

/** "HH:MM" → minutes since midnight, or null if unparseable. */
export function parseHM(s: string): number | null {
  const m = /^(\d{1,2}):(\d{2})/.exec(s.trim());
  if (!m) return null;
  return Number(m[1]) * 60 + Number(m[2]);
}

/** Minutes → "1h 5m" / "5m". */
export function fmtDur(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

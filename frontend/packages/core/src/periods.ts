/**
 * Date ranges for the income statement, as ISO `YYYY-MM-DD` strings.
 *
 * Everything is done on calendar fields of the *local* date, never through
 * `Date.toISOString()`, which flips to UTC and hands you yesterday at 00:30.
 */

export type Period = { start: string; end: string };

export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function today(now: Date = new Date()): string {
  return isoDate(now);
}

function lastDayOfMonth(year: number, monthIndex: number): Date {
  return new Date(year, monthIndex + 1, 0);
}

export type PeriodPreset =
  | "this-month"
  | "last-month"
  | "this-quarter"
  | "last-quarter"
  | "year-to-date"
  | "last-year"
  | "last-12-months";

export const PERIOD_PRESETS: readonly { id: PeriodPreset; label: string }[] = [
  { id: "this-month", label: "This month" },
  { id: "last-month", label: "Last month" },
  { id: "this-quarter", label: "This quarter" },
  { id: "last-quarter", label: "Last quarter" },
  { id: "year-to-date", label: "Year to date" },
  { id: "last-year", label: "Last year" },
  { id: "last-12-months", label: "Last 12 months" },
];

export function periodFor(preset: PeriodPreset, now: Date = new Date()): Period {
  const y = now.getFullYear();
  const m = now.getMonth();
  const q = Math.floor(m / 3) * 3;
  switch (preset) {
    case "this-month":
      return { start: isoDate(new Date(y, m, 1)), end: isoDate(now) };
    case "last-month":
      return { start: isoDate(new Date(y, m - 1, 1)), end: isoDate(lastDayOfMonth(y, m - 1)) };
    case "this-quarter":
      return { start: isoDate(new Date(y, q, 1)), end: isoDate(now) };
    case "last-quarter":
      return { start: isoDate(new Date(y, q - 3, 1)), end: isoDate(lastDayOfMonth(y, q - 1)) };
    case "year-to-date":
      return { start: isoDate(new Date(y, 0, 1)), end: isoDate(now) };
    case "last-year":
      return { start: `${y - 1}-01-01`, end: `${y - 1}-12-31` };
    case "last-12-months":
      return { start: isoDate(new Date(y - 1, m, now.getDate())), end: isoDate(now) };
  }
}

/** Which preset a range corresponds to, if any; for highlighting the button. */
export function presetOf(period: Period, now: Date = new Date()): PeriodPreset | null {
  for (const { id } of PERIOD_PRESETS) {
    const p = periodFor(id, now);
    if (p.start === period.start && p.end === period.end) return id;
  }
  return null;
}

export function isIsoDate(s: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = s.split("-").map(Number) as [number, number, number];
  const date = new Date(y, m - 1, d);
  return date.getFullYear() === y && date.getMonth() === m - 1 && date.getDate() === d;
}

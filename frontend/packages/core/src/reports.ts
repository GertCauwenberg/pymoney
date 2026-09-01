/**
 * Pure helpers over the report payloads. No React, no DOM, so a mobile app can
 * use the same grouping and the same CSV.
 */
import { formatAmount } from "./money";
import type { Account, AccountClass, Amount, ReportRow } from "./types";

export const CLASS_LABELS: Record<AccountClass, string> = {
  ASSET: "Assets",
  LIABILITY: "Liabilities",
  EQUITY: "Equity",
  INCOME: "Income",
  EXPENSE: "Expenses",
};

export interface Section {
  accountClass: AccountClass;
  label: string;
  rows: ReportRow[];
  /** Sum of the top-level rows' display values, when they share a commodity. */
  total: Amount | null;
}

/**
 * Split report rows into sections by account class.
 *
 * Rows carry a path and a depth but not a class; the account list does. Rows
 * whose account is unknown (a race with a concurrent edit) go to an "Other"
 * bucket rather than disappearing.
 */
export function sectionRows(
  rows: readonly ReportRow[],
  accounts: readonly Account[],
  order: readonly AccountClass[],
): Section[] {
  const classOf = new Map(accounts.map((a) => [a.id, a.account_class]));
  const buckets = new Map<AccountClass, ReportRow[]>(order.map((c) => [c, []]));
  for (const row of rows) {
    const cls = classOf.get(row.account_id);
    if (cls === undefined) continue;
    const bucket = buckets.get(cls);
    if (bucket) bucket.push(row);
    else buckets.set(cls, [row]);
  }
  return [...buckets.entries()]
    .filter(([, rows]) => rows.length > 0)
    .map(([accountClass, rows]) => ({
      accountClass,
      label: CLASS_LABELS[accountClass],
      rows,
      total: sumTopLevel(rows),
    }));
}

function sumTopLevel(rows: readonly ReportRow[]): Amount | null {
  const tops = rows.filter((r) => r.depth === 0);
  const first = tops[0];
  if (!first) return null;
  if (tops.some((r) => r.display_value.commodity_id !== first.display_value.commodity_id)) {
    return null;
  }
  const minor = tops.reduce((acc, r) => acc + r.display_value.minor, 0);
  return { ...first.display_value, minor, text: String(minor) };
}

/** Classes that are credit-normal: stored negative, read positive. */
export const CREDIT_NORMAL: ReadonlySet<AccountClass> = new Set(["LIABILITY", "EQUITY", "INCOME"]);

/**
 * `own_value` with the same sign convention as `display_value`.
 *
 * The server signs `display_value` to read naturally but leaves `own_value`
 * as stored, so a liability's own balance would come out negative next to a
 * positive subtree total. Derive the flip from the row itself when it can be
 * seen (a non-zero subtree), otherwise from the account class.
 */
export function displayOwnValue(row: ReportRow, accountClass: AccountClass): Amount {
  const flipped =
    row.value.minor !== 0 ? row.display_value.minor === -row.value.minor : CREDIT_NORMAL.has(accountClass);
  if (!flipped) return row.own_value;
  return naturalSign(row.own_value, "LIABILITY");
}

/**
 * An amount with the sign a human expects for the account it sits in: what
 * the server does for `display_value`, applied to register rows and balances.
 * Income, liabilities and equity are stored negative and read positive.
 */
export function naturalSign(amount: Amount, accountClass: AccountClass): Amount {
  if (!CREDIT_NORMAL.has(accountClass) || amount.minor === 0) return amount;
  const text = amount.text.startsWith("-") ? amount.text.slice(1) : `-${amount.text}`;
  return { ...amount, minor: -amount.minor, text };
}

/** The last segment of "Assets:Bank:Checking". */
export function leafName(path: string): string {
  const i = path.lastIndexOf(":");
  return i === -1 ? path : path.slice(i + 1);
}

/** Rows whose own value differs from the subtree total have children. */
export function hasChildren(row: ReportRow): boolean {
  return row.own_value.minor !== row.value.minor;
}

/**
 * Account ids whose rows are hidden when `collapsed` contains an ancestor.
 * Rows are in depth-first order, so an ancestor always precedes its subtree.
 */
export function visibleRows(rows: readonly ReportRow[], collapsed: ReadonlySet<string>): ReportRow[] {
  const out: ReportRow[] = [];
  let hideBelow: number | null = null;
  for (const row of rows) {
    if (hideBelow !== null) {
      if (row.depth > hideBelow) continue;
      hideBelow = null;
    }
    out.push(row);
    if (collapsed.has(row.account_id)) hideBelow = row.depth;
  }
  return out;
}

function csvCell(value: string): string {
  return /[",\n\r]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/** CSV with a header row; amounts as plain decimals with a dot, not localised. */
export function rowsToCsv(rows: readonly ReportRow[]): string {
  const header = ["path", "depth", "value", "own_value", "commodity"];
  const lines = rows.map((r) =>
    [
      csvCell(r.path),
      String(r.depth),
      formatAmount(r.display_value, { locale: "en-US" }).replace(/,/g, ""),
      formatAmount(r.own_value, { locale: "en-US" }).replace(/,/g, ""),
      r.display_value.commodity,
    ].join(","),
  );
  return [header.join(","), ...lines].join("\r\n") + "\r\n";
}

// --- pies -----------------------------------------------------------------------

export interface PieSlice {
  label: string;
  /** Minor units, positive. */
  minor: number;
  /** 0..1 share of the whole. */
  share: number;
  /** Account id when the slice is one account; absent for "Other". */
  accountId?: string;
}

/** Slices smaller than this share are gathered into one "Other" slice. */
export const OTHER_BELOW = 0.02;

/**
 * Turn labelled figures into pie slices: positive only, largest first, the
 * slivers folded into one "Other". A category whose net for the period is a
 * refund has no share of "where the money went" and is left out rather than
 * drawn as a negative wedge.
 */
export function pieSlices(
  labelled: readonly { label: string; minor: number; accountId?: string }[],
  otherLabel = "Other",
): PieSlice[] {
  const positive = labelled.filter((l) => l.minor > 0).sort((a, b) => b.minor - a.minor);
  const total = positive.reduce((acc, l) => acc + l.minor, 0);
  if (total === 0) return [];
  const big = positive.filter((l) => l.minor / total >= OTHER_BELOW);
  const rest = positive.filter((l) => l.minor / total < OTHER_BELOW);
  const kept = rest.length > 1 ? big : positive;
  const slices: PieSlice[] = kept.map((l) => ({
    label: l.label,
    minor: l.minor,
    share: l.minor / total,
    ...(l.accountId ? { accountId: l.accountId } : {}),
  }));
  if (rest.length > 1) {
    const other = rest.reduce((acc, l) => acc + l.minor, 0);
    slices.push({ label: otherLabel, minor: other, share: other / total });
  }
  return slices;
}

/**
 * Income or expense categories as pie slices: each account's *own* figure
 * (so a parent and its children are not both counted), read with the
 * class's natural sign.
 */
export function categorySlices(
  rows: readonly ReportRow[],
  accounts: readonly Account[],
  accountClass: "INCOME" | "EXPENSE",
  otherLabel = "Other",
): PieSlice[] {
  const classOf = new Map(accounts.map((a) => [a.id, a.account_class]));
  const sign = accountClass === "INCOME" ? -1 : 1;
  return pieSlices(
    rows
      .filter((r) => classOf.get(r.account_id) === accountClass)
      .map((r) => ({ label: r.path.split(":").pop() ?? r.path, minor: r.own_value.minor * sign, accountId: r.account_id })),
    otherLabel,
  );
}

export function slicesToCsv(slices: readonly PieSlice[], commodity: string): string {
  const lines = ["label,amount,share"];
  for (const s of slices) lines.push(`${JSON.stringify(s.label)},${s.minor},${(s.share * 100).toFixed(1)}%`);
  lines.push(`,,${commodity}`);
  return lines.join("\n");
}

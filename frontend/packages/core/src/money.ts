/**
 * Money for display.
 *
 * The server's rule is that an amount is never a float, and we keep it: every
 * operation here works on the integer `minor` count and the integer `scale`,
 * as BigInt, and produces a string. Nothing is ever divided.
 *
 * `Amount.text` is what the server thinks the amount looks like ("1234.50"). We
 * prefer to format ourselves so the grouping and decimal separators follow the
 * user's locale, but the digits come from the same exact integers.
 */
import type { Amount, Rate } from "./types";

export interface FormatOptions {
  locale?: string | undefined;
  /** Append the commodity code: "1,234.50 EUR". */
  withCommodity?: boolean;
  /** Show zero as an empty string, for quieter tables. */
  blankZero?: boolean;
  /** Parentheses instead of a minus sign, accountant style. */
  accounting?: boolean;
}

interface Separators {
  group: string;
  decimal: string;
  minus: string;
}

const separatorCache = new Map<string, Separators>();

function separators(locale: string | undefined): Separators {
  const key = locale ?? "";
  const cached = separatorCache.get(key);
  if (cached) return cached;
  const parts = new Intl.NumberFormat(locale, { signDisplay: "always" }).formatToParts(-1234567.5);
  const find = (type: string, fallback: string) =>
    parts.find((p) => p.type === type)?.value ?? fallback;
  const found: Separators = {
    group: find("group", ","),
    decimal: find("decimal", "."),
    minus: find("minusSign", "-"),
  };
  separatorCache.set(key, found);
  return found;
}

/** Number of decimal places implied by a scale of 10^n. */
export function decimalsOf(scale: number): number {
  let n = 0;
  let s = BigInt(scale);
  while (s > 1n) {
    s /= 10n;
    n += 1;
  }
  return n;
}

function groupDigits(digits: string, group: string): string {
  const out: string[] = [];
  for (let i = digits.length; i > 0; i -= 3) {
    out.unshift(digits.slice(Math.max(0, i - 3), i));
  }
  return out.join(group);
}

/**
 * Format exact minor units. `formatMinor(123456n, 100)` is "1,234.56" in an
 * English locale and "1.234,56" in a German one.
 */
export function formatMinor(
  minor: bigint | number,
  scale: number,
  options: FormatOptions = {},
): string {
  const value = typeof minor === "bigint" ? minor : BigInt(minor);
  if (options.blankZero && value === 0n) return "";
  const seps = separators(options.locale);
  const negative = value < 0n;
  const magnitude = (negative ? -value : value).toString();
  const decimals = decimalsOf(scale);
  const padded = magnitude.padStart(decimals + 1, "0");
  const whole = padded.slice(0, padded.length - decimals);
  const fraction = padded.slice(padded.length - decimals);
  let text = groupDigits(whole, seps.group);
  if (decimals > 0) text += seps.decimal + fraction;
  if (negative) text = options.accounting ? `(${text})` : seps.minus + text;
  return text;
}

export function formatAmount(amount: Amount, options: FormatOptions = {}): string {
  const text = formatMinor(amount.minor, amount.scale, options);
  if (!text) return text;
  return options.withCommodity ? `${text} ${amount.commodity}` : text;
}

export function isZero(amount: Amount): boolean {
  return amount.minor === 0;
}

export function isNegative(amount: Amount): boolean {
  return amount.minor < 0;
}

/** Same commodity and same minor count. */
export function sameAmount(a: Amount, b: Amount): boolean {
  return a.commodity_id === b.commodity_id && a.minor === b.minor;
}

/**
 * Exact plain rendering of minor units ("1234.50", "-200.00"), matching the
 * server's `AmountOut.text`. A derived amount must carry one, or the tooltip
 * in `<Money>` shows the figure it was derived from.
 */
function textOf(minor: bigint, scale: number): string {
  const decimals = decimalsOf(scale);
  const negative = minor < 0n;
  const magnitude = (negative ? -minor : minor).toString();
  const padded = magnitude.padStart(decimals + 1, "0");
  const whole = padded.slice(0, padded.length - decimals);
  const fraction = padded.slice(padded.length - decimals);
  const body = decimals > 0 ? `${whole}.${fraction}` : whole;
  return negative ? `-${body}` : body;
}

function withMinor(amount: Amount, minor: bigint): Amount {
  return { ...amount, minor: Number(minor), text: textOf(minor, amount.scale) };
}

/**
 * `a` minus `b`, where "nothing budgeted" counts as zero -- unless neither
 * side has a figure, in which case there is nothing to say.
 *
 * This is how a client turns two parallel plan arrays into a difference, such
 * as a savings figure from `income.budget` and `expenses.budget`.
 */
export function subtractAmounts(a: Amount | null, b: Amount | null): Amount | null {
  if (a === null && b === null) return null;
  if (a === null) return withMinor(b!, -BigInt(b!.minor));
  return withMinor(a, BigInt(a.minor) - BigInt(b?.minor ?? 0));
}

/**
 * What is left of a plan: `budget` minus `spent`, or null where there is no
 * plan to measure against -- never spend measured against nothing.
 *
 * `BudgetTableOut` carries plans and movement only, so this is the contract
 * every budget client applies to a row (`budget` against the same-indexed
 * `period.rows` display_cells) and to a class (`budget` against `spent`).
 */
export function budgetRemaining(budget: Amount | null, spent: Amount): Amount | null {
  return budget === null ? null : subtractAmounts(budget, spent);
}

/**
 * The null-skipping sum of a column: null where nothing is budgeted, and
 * otherwise the total over the entries that have one.
 *
 * This is how a client closes a per-column walk into a whole-period figure,
 * since `BudgetTableOut` no longer appends a total to every array.
 */
export function sumAmounts(values: readonly (Amount | null)[]): Amount | null {
  let first: Amount | null = null;
  let sum = 0n;
  for (const value of values) {
    if (value === null) continue;
    if (first === null) first = value;
    sum += BigInt(value.minor);
  }
  return first === null ? null : withMinor(first, sum);
}

/**
 * A rate as a percentage with fixed decimals, computed from the exact
 * `num`/`den` pair by integer arithmetic, rounding half away from zero.
 */
export function formatRatePercent(rate: Rate, decimals = 1, locale?: string): string {
  const num = BigInt(rate.num) * 100n * 10n ** BigInt(decimals);
  const den = BigInt(rate.den);
  if (den === 0n) return "—";
  const negative = num < 0n !== den < 0n;
  let q = (num < 0n ? -num : num) / (den < 0n ? -den : den);
  const r = (num < 0n ? -num : num) % (den < 0n ? -den : den);
  if (r * 2n >= (den < 0n ? -den : den)) q += 1n;
  const scale = Number(10n ** BigInt(decimals));
  return `${formatMinor(negative ? -q : q, scale, { locale })}\u00a0%`;
}

/** The decimal approximation the server already computed, trimmed. */
export function formatRate(rate: Rate, maxDecimals = 6): string {
  const [whole, fraction = ""] = rate.approx.split(".");
  const trimmed = fraction.slice(0, maxDecimals).replace(/0+$/, "");
  return trimmed ? `${whole}.${trimmed}` : (whole ?? "0");
}

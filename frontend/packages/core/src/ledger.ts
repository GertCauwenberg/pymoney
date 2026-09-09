/**
 * Editing transactions: the arithmetic and the rules, without any UI.
 *
 * A form holds amounts as the strings the user typed. Nothing here turns them
 * into a JavaScript number: a decimal is parsed into integer minor units as a
 * BigInt, summed as a BigInt, and sent to the server as the string it was --
 * the server's rule that an amount is never a float holds on the way in as
 * well as on the way out.
 *
 * The server books the legs and enforces the real invariants (balance, one
 * residual leg, accounts that accept postings). What lives here is only what
 * a form needs to say *before* sending: which legs are incomplete, whether the
 * entry balances, and how to turn a stored transaction back into a draft.
 */
import { decimalsOf, formatMinor } from "./money";
import type { Account, Amount, Commodity, LegIn, Split, Transaction, TransactionIn } from "./types";

/** One leg as the user is editing it. Amounts are the typed text. */
export interface LegDraft {
  /** Stable key for React lists; not sent. */
  key: string;
  account_id: string;
  /** In the transaction currency. "" means "balance the entry with this leg". */
  value: string;
  /**
   * In the account's own commodity, only meaningful when that differs from the
   * transaction currency (a USD wallet in a EUR book). Optional even then: left
   * blank, the server values the leg from the book's own price history; filled
   * in, the value over the quantity becomes a "transaction" price for the day.
   * "" otherwise.
   */
  quantity: string;
  memo: string;
  /** Ids of existing tags on this leg, in the order the user arranged them. */
  tag_ids: string[];
  /**
   * Names typed that do not match a tag the book has yet; created just before
   * the transaction is posted and then folded into `tag_ids` -- the twin of
   * `TransactionDraft.payee_id === "__new__"`.
   */
  new_tag_names: string[];
  /**
   * What the form does not edit but must not lose when it re-sends the legs:
   * a split's reconciliation mark, action, cheque number and per-split payee.
   * (Not the stated price: after an edit to the amounts it may no longer agree
   * with them, and the server would rightly refuse the contradiction.)
   */
  keep: Pick<LegIn, "reconcile_state" | "reconcile_date" | "action" | "number" | "payee_id">;
}

const FRESH: LegDraft["keep"] = { reconcile_state: "NOT_RECONCILED", reconcile_date: null, action: "", number: "", payee_id: null };

export interface TransactionDraft {
  date: string;
  payee_id: string | null;
  memo: string;
  number: string;
  legs: LegDraft[];
}

let counter = 0;
export function newLegKey(): string {
  counter += 1;
  return `leg-${counter}`;
}

export function emptyLeg(account_id = ""): LegDraft {
  return { key: newLegKey(), account_id, value: "", quantity: "", memo: "", tag_ids: [], new_tag_names: [], keep: FRESH };
}

/**
 * A draft for a new entry from an account's register: that account is the
 * first leg, a blank second leg waits for the category.
 */
export function newDraft(accountId: string, date: string): TransactionDraft {
  return { date, payee_id: null, memo: "", number: "", legs: [emptyLeg(accountId), emptyLeg()] };
}

const DECIMAL = /^(-?)(\d*)(?:\.(\d*))?$/;

/**
 * Parse a typed decimal into minor units, exactly. `parseDecimal("12.5", 100)`
 * is `1250n`; `"12.345"` at scale 100 is `null` (it would need rounding, which
 * is the server's decision, not the form's); `""` is `null`.
 */
export function parseDecimal(text: string, scale: number): bigint | null {
  const m = DECIMAL.exec(text.trim().replace(",", "."));
  if (!m) return null;
  const [, sign, whole = "", fraction = ""] = m;
  if (whole === "" && fraction === "") return null;
  const decimals = decimalsOf(scale);
  if (fraction.length > decimals) return null;
  const minor = BigInt((whole || "0") + fraction.padEnd(decimals, "0"));
  return sign === "-" ? -minor : minor;
}

/** Minor units back to the plain decimal the server accepts ("-84.20"). */
export function minorToDecimal(minor: number | bigint, scale: number): string {
  return formatMinor(minor, scale, { locale: "en-US" }).replace(/,/g, "");
}

/** Does this leg's account hold something other than the transaction currency? */
export function needsQuantity(leg: LegDraft, accounts: ReadonlyMap<string, Account>, currencyId: string): boolean {
  const account = accounts.get(leg.account_id);
  return account !== undefined && account.commodity_id !== currencyId;
}

export interface DraftProblem {
  /** Index into `legs`, or null for the transaction as a whole. */
  leg: number | null;
  message: string;
}

/**
 * What stops the draft being sent. Empty means "try the server", which still
 * has the final say (a closed account, a reconciled transaction, a rounding
 * problem).
 */
export function validateDraft(
  draft: TransactionDraft,
  accounts: ReadonlyMap<string, Account>,
  currency: Commodity,
  commodities: ReadonlyMap<string, Commodity>,
): DraftProblem[] {
  const problems: DraftProblem[] = [];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.date)) problems.push({ leg: null, message: "A date is needed." });
  if (draft.legs.length < 2) problems.push({ leg: null, message: "A transaction needs at least two legs." });

  let blanks = 0;
  draft.legs.forEach((leg, i) => {
    if (!leg.account_id) {
      problems.push({ leg: i, message: "Choose an account." });
      return;
    }
    const account = accounts.get(leg.account_id);
    if (!account) {
      problems.push({ leg: i, message: "Unknown account." });
      return;
    }
    if (account.placeholder) problems.push({ leg: i, message: `${account.path} is a placeholder and takes no postings.` });
    if (account.closed) problems.push({ leg: i, message: `${account.path} is closed.` });
    if (leg.value.trim() === "") {
      blanks += 1;
    } else if (parseDecimal(leg.value, currency.minor_unit_factor) === null) {
      problems.push({ leg: i, message: `Not an amount in ${currency.code} (up to ${decimalsOf(currency.minor_unit_factor)} decimals).` });
    }
    if (needsQuantity(leg, accounts, currency.id) && leg.quantity.trim() !== "") {
      const own = commodities.get(account.commodity_id);
      if (own && parseDecimal(leg.quantity, own.minor_unit_factor) === null) {
        problems.push({ leg: i, message: `Not an amount in ${account.commodity}.` });
      }
    }
  });
  if (blanks > 1) problems.push({ leg: null, message: "Only one leg may be left blank to balance the entry." });
  return problems;
}

/**
 * The sum of the filled-in values, exactly. What a blank leg will receive is
 * its negation; without a blank leg, a non-zero sum means the entry does not
 * balance and the server will refuse it.
 */
export function draftBalance(draft: TransactionDraft, currency: Commodity): { sum: bigint; blank: boolean } {
  let sum = 0n;
  let blank = false;
  for (const leg of draft.legs) {
    if (leg.value.trim() === "") {
      blank = true;
      continue;
    }
    const minor = parseDecimal(leg.value, currency.minor_unit_factor);
    if (minor !== null) sum += minor;
  }
  return { sum, blank };
}

export function balanceAmount(sum: bigint, currency: Commodity): Amount {
  return {
    commodity_id: currency.id,
    commodity: currency.code,
    minor: Number(sum),
    scale: currency.minor_unit_factor,
    text: minorToDecimal(sum, currency.minor_unit_factor),
  };
}

function legToWire(leg: LegDraft, accounts: ReadonlyMap<string, Account>, currencyId: string): LegIn {
  const value = leg.value.trim().replace(",", ".");
  const quantity = needsQuantity(leg, accounts, currencyId) ? leg.quantity.trim().replace(",", ".") : "";
  return {
    account_id: leg.account_id,
    // null, not "": a blank value is the residual leg, and the server wants
    // the field absent or null for that.
    value: value === "" ? null : value,
    quantity: quantity === "" ? null : quantity,
    memo: leg.memo.trim(),
    // Only resolved ids travel: any new_tag_names must have been created and
    // folded into tag_ids before the draft is sent.
    tag_ids: [...leg.tag_ids],
    ...leg.keep,
  };
}

/** The body for `POST /transactions`. Call `validateDraft` first. */
export function draftToTransactionIn(
  draft: TransactionDraft,
  accounts: ReadonlyMap<string, Account>,
  currency: Commodity,
): TransactionIn {
  return {
    date: draft.date,
    legs: draft.legs.map((leg) => legToWire(leg, accounts, currency.id)),
    currency_id: currency.id,
    memo: draft.memo.trim(),
    payee_id: draft.payee_id,
    number: draft.number.trim(),
    rounding: "HALF_EVEN",
  };
}

/**
 * A stored transaction as a draft, for editing. Values come back as the exact
 * decimals of their minor units, so a round trip without edits is a no-op on
 * the server (it compares the booked figures and burns no revision).
 */
export function draftFromTransaction(tx: Transaction, currencyId: string): TransactionDraft {
  return {
    date: tx.date,
    payee_id: tx.payee_id ?? null,
    memo: tx.memo,
    number: tx.number,
    legs: tx.splits.map((s: Split) => ({
      key: s.id,
      account_id: s.account_id,
      value: minorToDecimal(s.value.minor, s.value.scale),
      quantity: s.quantity.commodity_id === currencyId ? "" : minorToDecimal(s.quantity.minor, s.quantity.scale),
      memo: s.memo,
      tag_ids: [...(s.tag_ids ?? [])],
      new_tag_names: [],
      keep: {
        reconcile_state: s.reconcile_state,
        reconcile_date: s.reconcile_date ?? null,
        action: s.action,
        number: s.number,
        payee_id: s.payee_id ?? null,
      },
    })),
  };
}

/** Accounts a leg may be posted to: open, not a placeholder, sorted by path. */
export function postableAccounts(accounts: readonly Account[]): Account[] {
  return accounts.filter((a) => !a.placeholder && !a.closed).sort((a, b) => a.path.localeCompare(b.path));
}

/**
 * E0.0 runtime probe: run the real `@pymoney/core` money/ledger code on the
 * JavaScript engine this app is actually executing on (Hermes on device,
 * JSC/Hermes on simulator, Node in a typecheck-only world).
 *
 * Every check is a pure function of the imported modules — no reimplemented
 * arithmetic — so a pass means `money.ts`/`ledger.ts` work here.
 */
import {
  budgetRemaining,
  draftBalance,
  formatAmount,
  formatMinor,
  formatRatePercent,
  parseDecimal,
  subtractAmounts,
  sumAmounts,
  validateDraft,
  emptyLeg,
  type Account,
  type Amount,
  type Commodity,
  type TransactionDraft,
} from "@pymoney/core";

export interface ProbeResult {
  name: string;
  pass: boolean;
  detail: string;
}

export interface ProbeReport {
  engine: string;
  isHermes: boolean;
  results: ProbeResult[];
  passed: number;
  failed: number;
  /** formatAmount renders of the spike amounts — what the screen must show. */
  rendered: { label: string; text: string }[];
}

const eur: Commodity = {
  id: "eur",
  code: "EUR",
  name: "Euro",
  kind: "CURRENCY",
  symbol: "€",
  minor_unit_factor: 100,
  display_precision: 2,
  isin: null,
  quote_source: null,
  revision: 1,
};

function amount(minor: number, scale = 100): Amount {
  return {
    commodity_id: "eur",
    commodity: "EUR",
    minor,
    scale,
    text: "",
  };
}

/** AmountOut as the wire sends it (integer minor + scale). */
const spikeAmounts: Amount[] = [
  amount(123450), // 1234.50
  amount(-5), // -0.05
  amount(0),
  // 2^53+1 is only reachable through formatMinor(bigint): Amount.minor is a
  // JSON integer and cannot carry it. Covered in the probe below.
  { ...amount(129999), scale: 10000 }, // 12.9999
];

function check(name: string, fn: () => string | true, results: ProbeResult[]): void {
  try {
    const out = fn();
    if (out === true) {
      results.push({ name, pass: true, detail: "ok" });
    } else {
      results.push({ name, pass: false, detail: out });
    }
  } catch (e) {
    results.push({ name, pass: false, detail: String(e) });
  }
}

export function runProbes(): ProbeReport {
  const results: ProbeResult[] = [];
  const isHermes =
    typeof (globalThis as { HermesInternal?: unknown }).HermesInternal !== "undefined";
  const engine = isHermes
    ? "Hermes"
    : typeof navigator !== "undefined" && /node/i.test(navigator.userAgent ?? "")
      ? "Node"
      : "unknown JS engine";

  // ---- formatAmount / formatMinor ---------------------------------------

  check(
    "formatAmount: 123450 @100 en-US",
    () => {
      const text = formatAmount(spikeAmounts[0]!, { locale: "en-US" });
      return text === "1,234.50" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  check(
    "formatAmount: negative + accounting",
    () => {
      const text = formatAmount(spikeAmounts[1]!, { locale: "en-US", accounting: true });
      return text === "(0.05)" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  check(
    "formatMinor: 2^53+1 keeps the last digit (BigInt, not float)",
    () => {
      const text = formatMinor(9007199254740993n, 100, { locale: "en-US" });
      return text === "90,071,992,547,409.93" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  check(
    "formatMinor: de-DE separators",
    () => {
      const text = formatMinor(123456789, 100, { locale: "de-DE" });
      return text === "1.234.567,89" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  check(
    "formatMinor: scale 10000",
    () => {
      const text = formatMinor(129999, 10000, { locale: "en-US" });
      return text === "12.9999" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  check(
    "formatRatePercent: 309797/320000 @1 → 96.8 %",
    () => {
      const text = formatRatePercent({ num: 309797, den: 320000, approx: "0.9681156250" }, 1, "en-US");
      return text === "96.8\u00a0%" ? true : `got ${JSON.stringify(text)}`;
    },
    results,
  );

  // ---- parseDecimal / draftBalance / validateDraft -----------------------

  check(
    "parseDecimal: \"12.5\" @100 → 1250n",
    () => {
      const v = parseDecimal("12.5", 100);
      return v === 1250n ? true : `got ${String(v)}`;
    },
    results,
  );

  check(
    "parseDecimal: \"12.345\" @100 → null (would need rounding)",
    () => {
      const v = parseDecimal("12.345", 100);
      return v === null ? true : `got ${String(v)}`;
    },
    results,
  );

  check(
    "parseDecimal: comma decimal \"12,5\" @100 → 1250n",
    () => {
      const v = parseDecimal("12,5", 100);
      return v === 1250n ? true : `got ${String(v)}`;
    },
    results,
  );

  check(
    "draftBalance: sum + blank leg is BigInt",
    () => {
      const draft: TransactionDraft = {
        date: "2026-10-16",
        payee_id: null,
        memo: "spike",
        number: "",
        legs: [
          { ...emptyLeg("chk"), value: "12.50" },
          { ...emptyLeg("food"), value: "3.25" },
          { ...emptyLeg("wallet"), value: "" },
        ],
      };
      const { sum, blank } = draftBalance(draft, eur);
      return sum === 1575n && blank ? true : `sum=${sum} blank=${blank}`;
    },
    results,
  );

  check(
    "validateDraft: two blank legs refused",
    () => {
      const checking: Account = {
        id: "chk",
        name: "Checking",
        path: "Assets:Checking",
        type: "CHECKING",
        account_class: "ASSET",
        parent_id: null,
        commodity_id: "eur",
        commodity: "EUR",
        description: "",
        code: "",
        closed: false,
        placeholder: false,
        notes: "",
        institution: "",
        iban: "",
        revision: 1,
      };
      const accounts = new Map<string, Account>([["chk", checking]]);
      const draft: TransactionDraft = {
        date: "2026-10-16",
        payee_id: null,
        memo: "",
        number: "",
        legs: [emptyLeg("chk"), emptyLeg("chk")],
      };
      const problems = validateDraft(draft, accounts, eur, new Map([["eur", eur]]));
      const msg = problems.map((p) => p.message).join("; ");
      return msg.includes("Only one leg") ? true : `got ${JSON.stringify(msg)}`;
    },
    results,
  );

  check(
    "budgetRemaining / subtractAmounts / sumAmounts",
    () => {
      const left = budgetRemaining(amount(4500), amount(1250));
      const s = sumAmounts([amount(100), null, amount(200)]);
      const d = subtractAmounts(amount(10), null);
      if (left?.minor !== 3250) return `left=${left?.minor}`;
      if (s?.minor !== 300) return `sum=${s?.minor}`;
      if (d?.minor !== 10) return `sub=${d?.minor}`;
      return true;
    },
    results,
  );

  // ---- Intl feature surface (what money.ts:separators() needs) ----------

  check(
    "Intl.NumberFormat.formatToParts + signDisplay:always",
    () => {
      const parts = new Intl.NumberFormat("en-US", { signDisplay: "always" }).formatToParts(-1234567.5);
      const find = (type: string) => parts.find((p) => p.type === type)?.value;
      const group = find("group");
      const decimal = find("decimal");
      const minus = find("minusSign");
      if (group !== "," ) return `group=${group}`;
      if (decimal !== ".") return `decimal=${decimal}`;
      if (minus !== "-") return `minus=${minus}`;
      return true;
    },
    results,
  );

  const rendered = [
    ...spikeAmounts.map((a, i) => ({
      label: `formatAmount[${i}] minor=${a.minor} scale=${a.scale}`,
      text: formatAmount(a, { locale: "en-US", withCommodity: true }),
    })),
    {
      label: "formatAmount de-DE",
      text: formatAmount(spikeAmounts[0]!, { locale: "de-DE" }),
    },
    {
      label: "formatRatePercent",
      text: formatRatePercent({ num: 309797, den: 320000, approx: "0.968" }, 1, "en-US"),
    },
  ];

  const passed = results.filter((r) => r.pass).length;
  return {
    engine,
    isHermes,
    results,
    passed,
    failed: results.length - passed,
    rendered,
  };
}

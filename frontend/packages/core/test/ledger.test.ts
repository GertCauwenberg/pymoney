import {
  draftBalance,
  draftFromTransaction,
  draftToTransactionIn,
  emptyLeg,
  minorToDecimal,
  newDraft,
  parseDecimal,
  postableAccounts,
  validateDraft,
} from "../src/ledger";
import type { Account, Commodity, Transaction } from "../src/types";

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
const usd: Commodity = { ...eur, id: "usd", code: "USD", name: "US Dollar", symbol: "$" };
const jpy: Commodity = { ...eur, id: "jpy", code: "JPY", name: "Yen", minor_unit_factor: 1, display_precision: 0 };
const commodities = new Map([eur, usd, jpy].map((c) => [c.id, c]));

function account(id: string, path: string, extra: Partial<Account> = {}): Account {
  return {
    id,
    name: path.split(":").pop()!,
    path,
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
    ...extra,
  };
}

const accounts = new Map(
  [
    account("assets", "Assets", { placeholder: true }),
    account("chk", "Assets:Checking"),
    account("wallet", "Assets:Dollar Wallet", { commodity_id: "usd", commodity: "USD" }),
    account("old", "Assets:Old", { closed: true }),
    account("food", "Expenses:Food", { type: "EXPENSE", account_class: "EXPENSE" }),
  ].map((a) => [a.id, a]),
);

describe("parseDecimal", () => {
  it("turns typed decimals into exact minor units", () => {
    expect(parseDecimal("12.5", 100)).toBe(1250n);
    expect(parseDecimal("-84.20", 100)).toBe(-8420n);
    expect(parseDecimal("0.05", 100)).toBe(5n);
    expect(parseDecimal(".5", 100)).toBe(50n);
    expect(parseDecimal("7", 100)).toBe(700n);
    expect(parseDecimal("1,5", 100)).toBe(150n);
    expect(parseDecimal("300", 1)).toBe(300n);
    // Beyond double precision, still exact.
    expect(parseDecimal("90071992547409.93", 100)).toBe(9007199254740993n);
  });

  it("refuses what would need rounding, and garbage", () => {
    expect(parseDecimal("12.345", 100)).toBeNull();
    expect(parseDecimal("1.5", 1)).toBeNull();
    expect(parseDecimal("", 100)).toBeNull();
    expect(parseDecimal("-", 100)).toBeNull();
    expect(parseDecimal("abc", 100)).toBeNull();
    expect(parseDecimal("1e3", 100)).toBeNull();
    expect(parseDecimal("12.3.4", 100)).toBeNull();
  });

  it("round-trips through minorToDecimal", () => {
    expect(minorToDecimal(-8420, 100)).toBe("-84.20");
    expect(minorToDecimal(123456789n, 100)).toBe("1234567.89");
    expect(minorToDecimal(5, 1)).toBe("5");
    expect(parseDecimal(minorToDecimal(-8420, 100), 100)).toBe(-8420n);
  });
});

describe("validateDraft", () => {
  it("accepts the common case: one amount, one blank leg", () => {
    const draft = newDraft("chk", "2026-03-01");
    draft.legs[0]!.value = "-84.20";
    draft.legs[1]!.account_id = "food";
    expect(validateDraft(draft, accounts, eur, commodities)).toEqual([]);
  });

  it("names the leg that is missing an account or has a bad amount", () => {
    const draft = newDraft("chk", "2026-03-01");
    draft.legs[0]!.value = "12.345";
    const problems = validateDraft(draft, accounts, eur, commodities);
    expect(problems).toEqual([
      { leg: 0, message: "Not an amount in EUR (up to 2 decimals)." },
      { leg: 1, message: "Choose an account." },
    ]);
  });

  it("allows at most one blank leg, and needs a date and two legs", () => {
    const draft = newDraft("chk", "");
    draft.legs[1]!.account_id = "food";
    draft.legs.push({ ...emptyLeg("food") });
    const messages = validateDraft(draft, accounts, eur, commodities).map((p) => p.message);
    expect(messages).toContain("A date is needed.");
    expect(messages).toContain("Only one leg may be left blank to balance the entry.");
    expect(validateDraft({ ...draft, legs: [draft.legs[0]!] }, accounts, eur, commodities).map((p) => p.message)).toContain(
      "A transaction needs at least two legs.",
    );
  });

  it("refuses placeholders and closed accounts before the server has to", () => {
    const draft = newDraft("assets", "2026-03-01");
    draft.legs[0]!.value = "1";
    draft.legs[1]!.account_id = "old";
    const messages = validateDraft(draft, accounts, eur, commodities).map((p) => p.message);
    expect(messages).toEqual(["Assets is a placeholder and takes no postings.", "Assets:Old is closed."]);
  });

  it("asks for a quantity when the account is not held in the transaction currency", () => {
    const draft = newDraft("chk", "2026-03-01");
    draft.legs[0]!.value = "-100";
    draft.legs[1]!.account_id = "wallet";
    expect(validateDraft(draft, accounts, eur, commodities)).toEqual([
      { leg: 1, message: "USD amount is needed: this account is not held in EUR." },
    ]);
    draft.legs[1]!.quantity = "108.50";
    expect(validateDraft(draft, accounts, eur, commodities)).toEqual([]);
    draft.legs[1]!.quantity = "108.505";
    expect(validateDraft(draft, accounts, eur, commodities)).toEqual([{ leg: 1, message: "Not an amount in USD." }]);
  });
});

describe("draftBalance", () => {
  it("sums exactly and reports whether a leg is blank", () => {
    const draft = newDraft("chk", "2026-03-01");
    draft.legs[0]!.value = "-10.10";
    draft.legs[1]!.account_id = "food";
    draft.legs[1]!.value = "10.10";
    expect(draftBalance(draft, eur)).toEqual({ sum: 0n, blank: false });
    draft.legs[1]!.value = "";
    expect(draftBalance(draft, eur)).toEqual({ sum: -1010n, blank: true });
    draft.legs[1]!.value = "0.1";
    expect(draftBalance(draft, eur)).toEqual({ sum: -1000n, blank: false });
  });
});

describe("draftToTransactionIn", () => {
  it("sends amounts as the strings typed, a blank value as null, and the quantity only where it applies", () => {
    const draft = newDraft("chk", "2026-03-01");
    draft.memo = " Rent ";
    draft.payee_id = "p1";
    draft.legs[0]!.value = "-100";
    draft.legs[0]!.quantity = "ignored: checking is in EUR";
    draft.legs[1]!.account_id = "wallet";
    draft.legs[1]!.quantity = "108,5";
    draft.legs[1]!.memo = "cash";
    const body = draftToTransactionIn(draft, accounts, eur);
    expect(body).toEqual({
      date: "2026-03-01",
      currency_id: "eur",
      memo: "Rent",
      number: "",
      payee_id: "p1",
      rounding: "HALF_EVEN",
      legs: [
        { account_id: "chk", value: "-100", quantity: null, memo: "", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null },
        { account_id: "wallet", value: null, quantity: "108.5", memo: "cash", action: "", number: "", reconcile_state: "NOT_RECONCILED", reconcile_date: null, payee_id: null },
      ],
    });
    // No floats anywhere in the payload.
    expect(JSON.stringify(body)).not.toMatch(/:\s*-?\d+\.\d+[,}]/);
  });
});

describe("draftFromTransaction", () => {
  const amount = (commodity_id: string, minor: number, scale = 100) => ({
    commodity_id,
    commodity: commodity_id.toUpperCase(),
    minor,
    scale,
    text: "",
  });
  const tx: Transaction = {
    id: "t1",
    date: "2026-02-01",
    currency_id: "eur",
    currency: "EUR",
    memo: "Cash for the trip",
    payee_id: null,
    number: "42",
    rounding: "HALF_EVEN",
    entered_at: null,
    imported_id: null,
    reconcile_state: "CLEARED",
    locked: false,
    revision: 3,
    splits: [
      {
        id: "s1",
        account_id: "chk",
        quantity: amount("eur", -10000),
        value: amount("eur", -10000),
        memo: "",
        payee_id: null,
        reconcile_state: "CLEARED",
        reconcile_date: "2026-02-03",
        action: "",
        number: "",
        price: null,
        implied_price: null,
      },
      {
        id: "s2",
        account_id: "wallet",
        quantity: amount("usd", 10850),
        value: amount("eur", 10000),
        memo: "",
        payee_id: "p9",
        reconcile_state: "NOT_RECONCILED",
        reconcile_date: null,
        action: "BUY",
        number: "",
        price: { num: 217, den: 200, approx: "1.0850000000" },
        implied_price: null,
      },
    ],
  };

  it("gives back exact decimals and keeps what the form does not edit", () => {
    const draft = draftFromTransaction(tx, "eur");
    expect(draft.date).toBe("2026-02-01");
    expect(draft.number).toBe("42");
    expect(draft.legs.map((l) => [l.account_id, l.value, l.quantity])).toEqual([
      ["chk", "-100.00", ""],
      ["wallet", "100.00", "108.50"],
    ]);
    expect(draft.legs[0]!.keep).toEqual({ reconcile_state: "CLEARED", reconcile_date: "2026-02-03", action: "", number: "", payee_id: null });
    expect(draft.legs[1]!.keep.action).toBe("BUY");
    expect(draft.legs[1]!.keep.payee_id).toBe("p9");
    // A round trip re-sends the marks, so an edit to the memo does not
    // silently un-clear a split.
    const body = draftToTransactionIn(draft, accounts, eur);
    expect(body.legs[0]!.reconcile_state).toBe("CLEARED");
    expect(body.legs[1]!.quantity).toBe("108.50");
    expect(body.legs[1]).not.toHaveProperty("price");
  });
});

describe("postableAccounts", () => {
  it("drops placeholders and closed accounts, sorted by path", () => {
    expect(postableAccounts([...accounts.values()]).map((a) => a.path)).toEqual([
      "Assets:Checking",
      "Assets:Dollar Wallet",
      "Expenses:Food",
    ]);
  });
});

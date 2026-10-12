import { csvCell, displayOwnValue, hasChildren, leafName, rowsToCsv, sectionRows, slicesToCsv, visibleRows } from "../src/reports";
import type { Account, Amount, ReportRow } from "../src/types";

const amt = (minor: number): Amount => ({
  commodity_id: "eur",
  commodity: "EUR",
  minor,
  scale: 100,
  text: String(minor / 100),
});

const row = (id: string, path: string, depth: number, value: number, own = value): ReportRow => ({
  account_id: id,
  path,
  depth,
  value: amt(value),
  display_value: amt(value),
  own_value: amt(own),
  qty: [amt(own)]
});

const account = (id: string, account_class: Account["account_class"]): Account =>
  ({ id, account_class }) as unknown as Account;

const rows = [
  row("a", "Assets", 0, 1000, 0),
  row("a1", "Assets:Bank", 1, 900),
  row("a2", "Assets:Cash", 1, 100),
  row("l", "Liabilities", 0, 50),
  row("e", "Equity", 0, -950),
];
const accounts = [account("a", "ASSET"), account("a1", "ASSET"), account("a2", "ASSET"), account("l", "LIABILITY"), account("e", "EQUITY")];

describe("sectionRows", () => {
  it("groups by class in the requested order and totals the top level", () => {
    const sections = sectionRows(rows, accounts, ["ASSET", "LIABILITY", "EQUITY"]);
    expect(sections.map((s) => s.label)).toEqual(["Assets", "Liabilities", "Equity"]);
    expect(sections[0]?.rows.map((r) => r.path)).toEqual(["Assets", "Assets:Bank", "Assets:Cash"]);
    expect(sections[0]?.total?.minor).toBe(1000);
  });
  it("drops empty sections and rows with no known account", () => {
    const sections = sectionRows(rows, accounts.slice(0, 3), ["LIABILITY", "ASSET"]);
    expect(sections.map((s) => s.accountClass)).toEqual(["ASSET"]);
  });
  it("has no total across mixed commodities", () => {
    const usd = { ...row("x", "Other", 0, 5), display_value: { ...amt(5), commodity_id: "usd" } };
    const sections = sectionRows([rows[0]!, usd], [accounts[0]!, account("x", "ASSET")], ["ASSET"]);
    expect(sections[0]?.total).toBeNull();
  });
});

describe("displayOwnValue", () => {
  const stored = (value: number, own: number, display: number): ReportRow => ({
    ...row("x", "X", 0, value, own),
    display_value: amt(display),
  });
  it("leaves debit-normal rows alone", () => {
    expect(displayOwnValue(stored(100, 40, 100), "ASSET").minor).toBe(40);
  });
  it("flips when the server flipped the subtree", () => {
    const out = displayOwnValue({ ...stored(-100, -40, 100), own_value: { ...amt(-40), text: "-0.40" } }, "LIABILITY");
    expect(out.minor).toBe(40);
    expect(out.text).toBe("0.40");
  });
  it("falls back to the class when the subtree is zero", () => {
    expect(displayOwnValue(stored(0, -40, 0), "INCOME").minor).toBe(40);
    expect(displayOwnValue(stored(0, -40, 0), "EXPENSE").minor).toBe(-40);
  });
});

describe("tree helpers", () => {
  it("leafName", () => {
    expect(leafName("Assets:Bank:Checking")).toBe("Checking");
    expect(leafName("Assets")).toBe("Assets");
  });
  it("hasChildren when own differs from subtree", () => {
    expect(hasChildren(rows[0]!)).toBe(true);
    expect(hasChildren(rows[1]!)).toBe(false);
  });
  it("visibleRows hides a collapsed subtree but not its siblings", () => {
    const shown = visibleRows(rows, new Set(["a"]));
    expect(shown.map((r) => r.path)).toEqual(["Assets", "Liabilities", "Equity"]);
    expect(visibleRows(rows, new Set()).length).toBe(rows.length);
  });
});

describe("csvCell", () => {
  it("quotes and doubles quotes only when needed", () => {
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell('a, "b"')).toBe('"a, ""b"""');
    expect(csvCell("line\nbreak")).toBe('"line\nbreak"');
  });

  it("neutralises anything a spreadsheet would read as a formula", () => {
    expect(csvCell("=cmd|' /C calc'!A0")).toBe("'=cmd|' /C calc'!A0");
    expect(csvCell("+1+1")).toBe("'+1+1");
    expect(csvCell("-2+3")).toBe("'-2+3");
    expect(csvCell("@SUM(A1)")).toBe("'@SUM(A1)");
    expect(csvCell("\t=1+1")).toBe("'\t=1+1");
    expect(csvCell("\r=1+1")).toBe(`"'\r=1+1"`); // quoted: it holds a CR
  });

  it("catches a formula behind leading whitespace", () => {
    expect(csvCell(" =1+1")).toBe("' =1+1");
  });

  it("neutralises and then quotes when both apply", () => {
    expect(csvCell('=HYPERLINK("http://evil.example","x")')).toBe(
      `"'=HYPERLINK(""http://evil.example"",""x"")"`,
    );
  });
});

describe("rowsToCsv", () => {
  it("writes a header, plain decimals and quotes what needs it", () => {
    const csv = rowsToCsv([row("q", 'Expenses:Food, "fancy"', 1, 123456)]);
    expect(csv).toBe(
      'path,depth,value,own_value,commodity\r\n"Expenses:Food, ""fancy""",1,1234.56,1234.56,EUR\r\n',
    );
  });

  it("neutralises formula-looking account paths and commodity codes", () => {
    const csv = rowsToCsv([{ ...row("q", "=cmd|' /C calc'!A0", 1, 123456), display_value: { ...amt(123456), commodity: "=EUR" } }]);
    expect(csv).toContain(`'=cmd|' /C calc'!A0,1,1234.56,1234.56,'=EUR`);
  });
});

describe("slicesToCsv", () => {
  it("neutralises formula-looking labels", () => {
    const csv = slicesToCsv([{ label: "=cmd|' /C calc'!A0", minor: 500, share: 1 }], "=EUR");
    expect(csv).toBe(`label,amount,share\n'=cmd|' /C calc'!A0,500,100.0%\n,,'=EUR`);
  });
});

describe("pieSlices", () => {
  it("keeps positives, sorts, and folds slivers into Other", async () => {
    const { pieSlices } = await import("../src/reports");
    const slices = pieSlices([
      { label: "Food", minor: 6000 },
      { label: "Refund", minor: -500 },
      { label: "Fuel", minor: 4000 },
      { label: "Tiny", minor: 50 },
      { label: "Tinier", minor: 50 },
    ]);
    expect(slices.map((s) => [s.label, s.minor])).toEqual([
      ["Food", 6000],
      ["Fuel", 4000],
      ["Other", 100],
    ]);
    expect(slices.reduce((a, s) => a + s.share, 0)).toBeCloseTo(1);
  });

  it("does not make an Other of a single sliver, and is empty without positives", async () => {
    const { pieSlices } = await import("../src/reports");
    expect(pieSlices([{ label: "A", minor: 10000 }, { label: "B", minor: 10 }]).map((s) => s.label)).toEqual(["A", "B"]);
    expect(pieSlices([{ label: "A", minor: 0 }, { label: "B", minor: -3 }])).toEqual([]);
  });
});

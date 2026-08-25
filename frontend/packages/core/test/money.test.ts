import { decimalsOf, formatAmount, formatMinor, formatRate, formatRatePercent } from "../src/money";
import type { Amount } from "../src/types";

const eur = (minor: number): Amount => ({
  commodity_id: "c",
  commodity: "EUR",
  minor,
  scale: 100,
  text: "",
});

describe("formatMinor", () => {
  it("never goes through a float", () => {
    // 2^53 + 1 is not representable as a double; BigInt keeps the last digit.
    expect(formatMinor(9007199254740993n, 100, { locale: "en-US" })).toBe("90,071,992,547,409.93");
  });

  it("pads small magnitudes with leading zeros", () => {
    expect(formatMinor(5, 100, { locale: "en-US" })).toBe("0.05");
    expect(formatMinor(-5, 100, { locale: "en-US" })).toBe("-0.05");
    expect(formatMinor(0, 100, { locale: "en-US" })).toBe("0.00");
  });

  it("follows the locale's separators", () => {
    expect(formatMinor(123456789, 100, { locale: "en-US" })).toBe("1,234,567.89");
    expect(formatMinor(123456789, 100, { locale: "de-DE" })).toBe("1.234.567,89");
    expect(formatMinor(123456789, 100, { locale: "fr-FR" })).toMatch(/^1.234.567,89$/u);
  });

  it("handles scales other than 100", () => {
    expect(formatMinor(129999, 10000, { locale: "en-US" })).toBe("12.9999");
    expect(formatMinor(7, 1, { locale: "en-US" })).toBe("7");
    expect(formatMinor(1234, 1000, { locale: "en-US" })).toBe("1.234");
  });

  it("offers accounting negatives and blank zeros", () => {
    expect(formatMinor(-1050, 100, { locale: "en-US", accounting: true })).toBe("(10.50)");
    expect(formatMinor(0, 100, { blankZero: true })).toBe("");
  });
});

describe("formatAmount", () => {
  it("appends the commodity on request", () => {
    expect(formatAmount(eur(123450), { locale: "en-US", withCommodity: true })).toBe("1,234.50 EUR");
  });
  it("keeps a blank zero blank even with a commodity", () => {
    expect(formatAmount(eur(0), { blankZero: true, withCommodity: true })).toBe("");
  });
});

describe("decimalsOf", () => {
  it.each([
    [1, 0],
    [10, 1],
    [100, 2],
    [10000, 4],
    [100000000, 8],
  ])("scale %i has %i decimals", (scale, decimals) => {
    expect(decimalsOf(scale)).toBe(decimals);
  });
});

describe("rates", () => {
  it("formats a savings rate as a rounded percentage, exactly", () => {
    expect(formatRatePercent({ num: 309797, den: 320000, approx: "0.9681156250" }, 1, "en-US")).toBe(
      "96.8\u00a0%",
    );
    expect(formatRatePercent({ num: 1, den: 3, approx: "0.3333333333" }, 2, "en-US")).toBe("33.33\u00a0%");
    expect(formatRatePercent({ num: -1, den: 8, approx: "-0.125" }, 1, "en-US")).toBe("-12.5\u00a0%");
    expect(formatRatePercent({ num: 1, den: 0, approx: "" })).toBe("—");
  });

  it("trims the server's decimal approximation", () => {
    expect(formatRate({ num: 1, den: 1, approx: "1.0000000000" })).toBe("1");
    expect(formatRate({ num: 108, den: 100, approx: "1.0800000000" })).toBe("1.08");
    expect(formatRate({ num: 1, den: 3, approx: "0.3333333333" }, 4)).toBe("0.3333");
  });
});

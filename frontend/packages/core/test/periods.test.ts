import { PERIOD_PRESETS, isIsoDate, isoDate, periodFor, presetOf } from "../src/periods";

const now = new Date(2026, 7, 24); // 24 August 2026, local time

describe("periodFor", () => {
  it("this month runs from the 1st to today", () => {
    expect(periodFor("this-month", now)).toEqual({ start: "2026-08-01", end: "2026-08-24" });
  });
  it("last month ends on its real last day", () => {
    expect(periodFor("last-month", now)).toEqual({ start: "2026-07-01", end: "2026-07-31" });
    expect(periodFor("last-month", new Date(2026, 2, 15))).toEqual({
      start: "2026-02-01",
      end: "2026-02-28",
    });
  });
  it("handles the year boundary", () => {
    expect(periodFor("last-month", new Date(2026, 0, 10))).toEqual({
      start: "2025-12-01",
      end: "2025-12-31",
    });
    expect(periodFor("last-quarter", new Date(2026, 1, 10))).toEqual({
      start: "2025-10-01",
      end: "2025-12-31",
    });
  });
  it("quarters", () => {
    expect(periodFor("this-quarter", now)).toEqual({ start: "2026-07-01", end: "2026-08-24" });
    expect(periodFor("last-quarter", now)).toEqual({ start: "2026-04-01", end: "2026-06-30" });
  });
  it("years", () => {
    expect(periodFor("year-to-date", now)).toEqual({ start: "2026-01-01", end: "2026-08-24" });
    expect(periodFor("last-year", now)).toEqual({ start: "2025-01-01", end: "2025-12-31" });
    expect(periodFor("last-12-months", now)).toEqual({ start: "2025-08-24", end: "2026-08-24" });
  });
  it("every preset round-trips through presetOf", () => {
    for (const { id } of PERIOD_PRESETS) {
      expect(presetOf(periodFor(id, now), now)).toBe(id);
    }
    expect(presetOf({ start: "2020-01-01", end: "2020-01-02" }, now)).toBeNull();
  });
});

describe("dates", () => {
  it("isoDate uses local calendar fields", () => {
    expect(isoDate(new Date(2026, 0, 1, 0, 30))).toBe("2026-01-01");
  });
  it("isIsoDate rejects shapes and impossible days", () => {
    expect(isIsoDate("2026-02-28")).toBe(true);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("2026-13-01")).toBe(false);
    expect(isIsoDate("26-01-01")).toBe(false);
    expect(isIsoDate("")).toBe(false);
  });
});

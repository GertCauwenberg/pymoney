/**
 * Pure-logic tests (vitest). No React Native imports — see vitest.config.ts.
 */
import { describe, expect, it } from "vitest";

import { resolveScheme } from "../../src/theme/scheme";

describe("resolveScheme", () => {
  it("maps the OS dark scheme to the dark token table", () => {
    expect(resolveScheme("dark")).toBe("dark");
  });

  it("treats light, unknown and absent schemes as light", () => {
    expect(resolveScheme("light")).toBe("light");
    expect(resolveScheme(null)).toBe("light");
    expect(resolveScheme(undefined)).toBe("light");
    expect(resolveScheme("DARK")).toBe("light");
    expect(resolveScheme("no-preference")).toBe("light");
  });
});

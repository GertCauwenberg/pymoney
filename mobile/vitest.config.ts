import { defineConfig } from "vitest/config";

/**
 * Pure-logic layer (E0.5): no React Native imports, so this runs on any Node
 * host including the Linux CI runner. Component tests live in
 * `tests/component/` under jest-expo.
 */
export default defineConfig({
  test: {
    name: "mobile-pure",
    globals: true,
    environment: "node",
    include: ["tests/pure/**/*.test.ts"],
  },
});

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    name: "tokens",
    globals: true,
    environment: "node",
    include: ["test/**/*.test.ts"],
  },
});

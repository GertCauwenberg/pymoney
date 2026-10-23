/**
 * Component layer (E0.5): jest-expo + @testing-library/react-native.
 * Pure logic stays in tests/pure (vitest) — keep RN imports out of that tree.
 *
 * transformIgnorePatterns intentionally omitted: jest-expo's preset already
 * transforms expo / react-native packages (including expo-modules-core). Overriding
 * it here re-introduces "Cannot use import statement outside a module".
 */
module.exports = {
  preset: "jest-expo",
  testMatch: ["**/tests/component/**/*.test.tsx", "**/tests/component/**/*.test.ts"],
  moduleNameMapper: {
    // One React: @pymoney/core resolves through frontend/node_modules, which
    // would otherwise load a second copy and break hooks ("Cannot read
    // properties of null (reading 'useCallback')").
    "^react$": "<rootDir>/node_modules/react",
    "^react/jsx-runtime$": "<rootDir>/node_modules/react/jsx-runtime",
    "^react/jsx-dev-runtime$": "<rootDir>/node_modules/react/jsx-dev-runtime",
    "^react-native$": "<rootDir>/node_modules/react-native",
    // Workspace packages ship TypeScript source; Metro transforms them for the
    // app, and jest must resolve the same entry points.
    "^@pymoney/core$": "<rootDir>/../frontend/packages/core/src/index.ts",
    "^@pymoney/core/hooks$": "<rootDir>/../frontend/packages/core/src/hooks/index.ts",
    "^@pymoney/tokens$": "<rootDir>/../frontend/packages/tokens/src/index.ts",
  },
};

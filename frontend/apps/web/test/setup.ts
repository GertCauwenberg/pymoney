import "@testing-library/jest-dom/vitest";

// Node 22+ ships an experimental `localStorage` on `globalThis`. Vitest's jsdom
// environment aliases `window` to `globalThis` and only bridges keys on its
// allow-list, so Node's getter wins and returns undefined without
// `--localstorage-file`. Rebind to the JSDOM Storage the environment already
// created — in-memory, per test file, no Node flag required.
const jsdomWindow = (globalThis as { jsdom?: { window: Window } }).jsdom?.window;
if (jsdomWindow) {
  const storage = jsdomWindow.localStorage;
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    enumerable: true,
    get: () => storage,
    set: () => {
      // Match populateGlobal: assignments are ignored in favour of jsdom's store.
    },
  });
}

const mockSecureData: Record<string, string> = {};
jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async (key: string) =>
    key in mockSecureData ? mockSecureData[key] : null,
  ),
  setItemAsync: jest.fn(async (key: string, value: string) => {
    mockSecureData[key] = value;
  }),
  deleteItemAsync: jest.fn(async (key: string) => {
    delete mockSecureData[key];
  }),
}));
jest.mock("expo-sqlite", () => require("./expoSqliteMock"));

const mockOpenURL = jest.fn();
jest.mock("expo-linking", () => {
  // eslint-disable-next-line no-console
  console.log("FACTORY RUN, mockOpenURL init?", typeof mockOpenURL);
  return {
    ...jest.requireActual("expo-linking"),
    openURL: (...args: unknown[]) => {
      // eslint-disable-next-line no-console
      console.log("ARROW CALLED with", args);
      return mockOpenURL(...args);
    },
  };
});

import { screen, userEvent } from "@testing-library/react-native";
import { renderRouter } from "expo-router/testing-library";

describe("probe", () => {
  it("what does the app see?", async () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const L = require("expo-linking");
    // eslint-disable-next-line no-console
    console.log("test-side openURL:", L.openURL.name);
    await renderRouter("app", { initialUrl: "/" });
    // eslint-disable-next-line no-console
    console.log("rendered");
    expect(true).toBe(true);
  });
});

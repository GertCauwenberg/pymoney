/**
 * Component tests (jest-expo + @testing-library/react-native).
 *
 * E0.5 harness smoke: prove the RN render path, the shared `t()` catalogue and
 * the session-expiry context all work under the test runner. Screens themselves
 * are exercised from E1 onward.
 */
import { useState } from "react";
import { Pressable, Text } from "react-native";
import { render, screen, userEvent } from "@testing-library/react-native";

import { I18nProvider, useT } from "@pymoney/core/hooks";

import {
  UnauthorizedProvider,
  useUnauthorized,
  type UnauthorizedEvent,
} from "../../src/api/unauthorized";
import { resolveScheme } from "../../src/theme/scheme";

function Banner() {
  const t = useT();
  const { event, consume } = useUnauthorized();
  const [count, setCount] = useState(0);
  if (!event) {
    return (
      <Pressable onPress={() => setCount((n) => n + 1)} testID="idle">
        <Text>{t("Log in")}</Text>
      </Pressable>
    );
  }
  return (
    <Pressable onPress={consume} testID="session-expired">
      <Text>{t("Session expired — sign in to sync")}</Text>
      <Text testID="taps">{String(count)}</Text>
    </Pressable>
  );
}

function Harness({ locale, event }: { locale: "en" | "nl"; event: UnauthorizedEvent | null }) {
  const [current, setCurrent] = useState(event);
  return (
    <I18nProvider locale={locale} onChange={() => undefined}>
      <UnauthorizedProvider value={{ event: current, consume: () => setCurrent(null) }}>
        <Banner />
      </UnauthorizedProvider>
    </I18nProvider>
  );
}

describe("session-expiry banner", () => {
  it("shows the English copy and dismisses on press", async () => {
    await render(<Harness locale="en" event={{ at: 1 }} />);
    expect(screen.getByText("Session expired — sign in to sync")).toBeTruthy();
    await userEvent.press(screen.getByTestId("session-expired"));
    expect(screen.getByText("Log in")).toBeTruthy();
  });

  it("renders Dutch through the shared catalogue", async () => {
    await render(<Harness locale="nl" event={{ at: 1 }} />);
    expect(screen.getByText("Sessie verlopen — meld u aan om te synchroniseren")).toBeTruthy();
  });

  it("keeps pure theme resolution independent of the RN colour scheme", () => {
    expect(resolveScheme("dark")).toBe("dark");
    expect(resolveScheme("light")).toBe("light");
  });
});

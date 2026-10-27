/**
 * Auth gate (E1.1): `Stack.Protected` routes signed-out users to `(auth)` and
 * signed-in users to the app, with the pending state held on a splash. This
 * doubles as the regression net for the expo-router "Maximum update depth"
 * loop that hit the earlier E1 attempt — a render loop fails these tests.
 */
import { Text } from "react-native";
import { Stack } from "expo-router";
import { renderRouter, screen } from "expo-router/testing-library";

function GateLayout({ authed }: { authed: boolean | null }) {
  if (authed === null) return <Text>splash</Text>;
  return (
    <Stack>
      <Stack.Protected guard={authed}>
        <Stack.Screen name="index" />
      </Stack.Protected>
      <Stack.Protected guard={!authed}>
        <Stack.Screen name="(auth)/login" />
        <Stack.Screen name="(auth)/forgot" />
      </Stack.Protected>
    </Stack>
  );
}

const routes = {
  index: () => <Text>home-screen</Text>,
  "(auth)/login": () => <Text>login-screen</Text>,
  "(auth)/forgot": () => <Text>forgot-screen</Text>,
};

describe("auth gate", () => {
  it("shows the splash while the session is pending", async () => {
    await renderRouter(
      { _layout: () => <GateLayout authed={null} />, ...routes },
      { initialUrl: "/" },
    );
    expect(screen.getByText("splash")).toBeTruthy();
  });

  it("routes to login when signed out", async () => {
    await renderRouter(
      { _layout: () => <GateLayout authed={false} />, ...routes },
      { initialUrl: "/" },
    );
    expect(screen.getByText("login-screen")).toBeTruthy();
  });

  it("shows the app when signed in", async () => {
    await renderRouter(
      { _layout: () => <GateLayout authed />, ...routes },
      { initialUrl: "/" },
    );
    expect(screen.getByText("home-screen")).toBeTruthy();
  });
});

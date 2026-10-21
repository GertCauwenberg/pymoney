/**
 * Root layout: the one place that owns the API client, the secure auth store,
 * and the `onUnauthorized` hook point (E0.2). Colours come from
 * `@pymoney/tokens` via `src/theme` (E0.3).
 */
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { ApiProvider, I18nProvider, createQueryClient, keys } from "@pymoney/core/hooks";
import type { Locale } from "@pymoney/core";

import { createApiClient } from "../src/api/client";
import { SecureAuthStore } from "../src/api/secureAuthStore";
import {
  UnauthorizedProvider,
  type UnauthorizedEvent,
} from "../src/api/unauthorized";
import { useColorSchemeName, useTheme, useThemedStyles, type Theme } from "../src/theme";

function createRootStyles(theme: Theme) {
  return StyleSheet.create({
    splash: {
      flex: 1,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: theme.colors.surface,
    },
  });
}

function Splash() {
  const theme = useTheme();
  const styles = useThemedStyles(createRootStyles);
  return (
    <View style={styles.splash}>
      <ActivityIndicator size="large" color={theme.colors.accent} />
    </View>
  );
}

export default function RootLayout() {
  const [locale, setLocale] = useState<Locale>("en");
  const [authReady, setAuthReady] = useState(false);
  const [event, setEvent] = useState<UnauthorizedEvent | null>(null);

  const scheme = useColorSchemeName();
  const theme = useTheme();
  const styles = useThemedStyles(createRootStyles);

  const auth = useMemo(() => new SecureAuthStore(), []);
  const queryClient = useMemo(() => createQueryClient(), []);

  const onUnauthorized = useCallback(() => {
    // M7: forget the token and prompt re-login. The outbox (E5) is preserved
    // by not touching anything but the credential and the session cache.
    auth.clear();
    queryClient.setQueryData(keys.session, null);
    setEvent({ at: Date.now() });
  }, [auth, queryClient]);

  const client = useMemo(() => createApiClient({ auth, onUnauthorized }), [auth, onUnauthorized]);

  useEffect(() => {
    let cancelled = false;
    void auth.ready().then(() => {
      if (!cancelled) setAuthReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, [auth]);

  const consume = useCallback(() => setEvent(null), []);
  const unauthorized = useMemo(() => ({ event, consume }), [event, consume]);

  if (!authReady) return <Splash />;

  return (
    <UnauthorizedProvider value={unauthorized}>
      <ApiProvider client={client} queryClient={queryClient}>
        <I18nProvider locale={locale} onChange={setLocale}>
          <StatusBar style={scheme === "dark" ? "light" : "dark"} />
          <Stack
            screenOptions={{
              headerStyle: { backgroundColor: theme.colors.accent },
              headerTintColor: theme.colors.accentText,
              contentStyle: { backgroundColor: styles.splash.backgroundColor },
            }}
          />
        </I18nProvider>
      </ApiProvider>
    </UnauthorizedProvider>
  );
}

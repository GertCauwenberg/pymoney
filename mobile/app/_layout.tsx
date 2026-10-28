/**
 * Root layout: the one place that owns the API client, the secure auth store,
 * the `onUnauthorized` hook point (E0.2) and the device book prefs (E1.2).
 * The auth gate (E1.1) is structural: `Stack.Protected` on the session, so a
 * dead token routes to `(auth)/login` and re-login resumes — never a
 * `useSegments` redirect. Colours come from `@pymoney/tokens` via
 * `src/theme` (E0.3).
 */
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { ApiProvider, I18nProvider, createQueryClient, keys, useT } from "@pymoney/core/hooks";
import type { Locale } from "@pymoney/core";

import { createApiClient } from "../src/api/client";
import { SecureAuthStore } from "../src/api/secureAuthStore";
import {
  UnauthorizedProvider,
  type UnauthorizedEvent,
} from "../src/api/unauthorized";
import { useAuthFlag } from "../src/auth/useAuthFlag";
import { BookPrefs } from "../src/data/bookPrefs";
import { BookPrefsProvider } from "../src/data/BookPrefsProvider";
import { SqliteKvStore } from "../src/data/sqliteKv";
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

function AppStack() {
  const t = useT();
  const theme = useTheme();
  const styles = useThemedStyles(createRootStyles);
  const authed = useAuthFlag();
  if (authed === null) return <Splash />;
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.accent },
        headerTintColor: theme.colors.accentText,
        contentStyle: { backgroundColor: styles.splash.backgroundColor },
      }}
    >
      <Stack.Protected guard={authed}>
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="books" options={{ title: t("Your books") }} />
        <Stack.Screen name="(book)/[bookId]" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Protected guard={!authed}>
        <Stack.Screen name="(auth)/login" options={{ headerShown: false }} />
        <Stack.Screen name="(auth)/forgot" options={{ headerShown: false }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  const [locale, setLocale] = useState<Locale>("en");
  const [authReady, setAuthReady] = useState(false);
  const [prefs, setPrefs] = useState<BookPrefs | null>(null);
  const [event, setEvent] = useState<UnauthorizedEvent | null>(null);

  const scheme = useColorSchemeName();
  const theme = useTheme();
  const styles = useThemedStyles(createRootStyles);

  const auth = useMemo(() => new SecureAuthStore(), []);
  const queryClient = useMemo(() => createQueryClient(), []);

  const onUnauthorized = useCallback(() => {
    // M7: forget the token and prompt re-login; the outbox (E5) is preserved
    // by not touching anything but the credential and the session cache. Only
    // a dead *session* raises the banner — a failed login attempt is answered
    // by the form's own error, and clearing an empty store is a no-op.
    const hadSession = auth.get().bearer !== undefined;
    auth.clear();
    queryClient.setQueryData(keys.session, null);
    if (hadSession) setEvent({ at: Date.now() });
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

  // E1.2: the book prefs live in the same `pymoney.db` the offline mirror
  // (E5.1) will use. Hydrated before the first screen so the initial-book
  // redirect never races the read.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const kv = await SqliteKvStore.open();
      const opened = new BookPrefs(kv);
      await opened.hydrate();
      if (!cancelled) setPrefs(opened);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const consume = useCallback(() => setEvent(null), []);
  const unauthorized = useMemo(() => ({ event, consume }), [event, consume]);

  if (!authReady || !prefs) return <Splash />;

  return (
    <UnauthorizedProvider value={unauthorized}>
      <ApiProvider client={client} queryClient={queryClient}>
        <BookPrefsProvider prefs={prefs}>
          <I18nProvider locale={locale} onChange={setLocale}>
            <StatusBar style={scheme === "dark" ? "light" : "dark"} />
            <AppStack />
          </I18nProvider>
        </BookPrefsProvider>
      </ApiProvider>
    </UnauthorizedProvider>
  );
}

/**
 * E0.2 acceptance shell: `useSession` / `useBooks` over the bearer client,
 * a minimal login so live data can be reached, and a session-expiry banner
 * driven by the `onUnauthorized` hook point. E1.1 replaces the form proper.
 * Styles come from `@pymoney/tokens` (E0.3).
 */
import { useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  useBooks,
  useI18n,
  useLogin,
  useLogout,
  useSession,
  useT,
} from "@pymoney/core/hooks";
import { LOCALES, formatAmount, type Amount } from "@pymoney/core";

import { useUnauthorized } from "../src/api/unauthorized";
import { useThemedStyles, type Theme } from "../src/theme";

const sample: Amount = {
  commodity_id: "eur",
  commodity: "EUR",
  minor: 123450,
  scale: 100,
  text: "1234.50",
};

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      paddingTop: t.spacing.xxl,
      backgroundColor: t.colors.surface,
      gap: t.spacing.lg,
    },
    title: {
      fontSize: 28,
      fontWeight: "700",
      color: t.colors.accent,
    },
    langRow: {
      flexDirection: "row",
      gap: t.spacing.sm,
    },
    langChip: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: t.radius.full,
      borderWidth: 1,
      borderColor: t.colors.accent,
    },
    langChipOn: {
      backgroundColor: t.colors.accent,
    },
    langChipText: {
      color: t.colors.accent,
      fontWeight: "600",
    },
    langChipTextOn: {
      color: t.colors.accentText,
    },
    card: {
      borderWidth: 1,
      borderColor: t.colors.border,
      borderRadius: t.radius.md,
      padding: 14,
      gap: t.spacing.sm,
    },
    label: {
      fontSize: 14,
      color: t.colors.text,
      fontWeight: "600",
    },
    value: {
      fontSize: 16,
      fontWeight: "600",
      color: t.colors.text,
    },
    money: {
      fontSize: 22,
      fontWeight: "700",
      fontVariant: ["tabular-nums"],
      color: t.colors.positive,
    },
    hint: {
      fontSize: 12,
      color: t.colors.muted,
    },
    error: {
      fontSize: 13,
      color: t.colors.negative,
    },
    input: {
      borderWidth: 1,
      borderColor: t.colors.borderStrong,
      borderRadius: t.radius.sm,
      paddingHorizontal: 12,
      paddingVertical: 10,
      fontSize: 16,
      color: t.colors.text,
      backgroundColor: t.colors.surface,
    },
    button: {
      backgroundColor: t.colors.accent,
      borderRadius: t.radius.sm,
      paddingHorizontal: 16,
      paddingVertical: 12,
      alignItems: "center",
    },
    buttonDisabled: {
      opacity: 0.5,
    },
    buttonText: {
      color: t.colors.accentText,
      fontWeight: "600",
      fontSize: 16,
    },
    banner: {
      backgroundColor: t.colors.negative,
      borderRadius: t.radius.sm,
      padding: 12,
    },
    bannerText: {
      color: t.colors.accentText,
      fontWeight: "600",
      textAlign: "center",
    },
    sessionBox: {
      gap: t.spacing.sm,
    },
    bookRow: {
      flexDirection: "row",
      justifyContent: "space-between",
      paddingVertical: 6,
      borderBottomWidth: StyleSheet.hairlineWidth,
      borderBottomColor: t.colors.border,
    },
    bookName: {
      fontSize: 16,
      fontWeight: "600",
      color: t.colors.text,
    },
    bookRole: {
      fontSize: 14,
      color: t.colors.muted,
    },
  });
}

function LanguageBar() {
  const styles = useThemedStyles(createStyles);
  const { locale, setLocale } = useI18n();
  return (
    <View style={styles.langRow} accessibilityRole="tablist">
      {LOCALES.map((l) => {
        const selected = l.code === locale;
        return (
          <Pressable
            key={l.code}
            onPress={() => setLocale(l.code)}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            style={[styles.langChip, selected && styles.langChipOn]}
          >
            <Text style={[styles.langChipText, selected && styles.langChipTextOn]}>
              {l.name}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function SessionExpiredBanner() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const { event, consume } = useUnauthorized();
  if (!event) return null;
  return (
    <Pressable
      accessibilityRole="alert"
      onPress={consume}
      style={styles.banner}
      testID="session-expired"
    >
      <Text style={styles.bannerText}>
        {t("Session expired — sign in to sync")}
      </Text>
    </Pressable>
  );
}

function LoginForm() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  return (
    <View style={styles.card}>
      <Text style={styles.label}>{t("Sign in")}</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        placeholder={t("Email")}
        placeholderTextColor={styles.hint.color}
        value={email}
        onChangeText={setEmail}
        testID="login-email"
      />
      <TextInput
        style={styles.input}
        autoComplete="password"
        placeholder={t("Password")}
        placeholderTextColor={styles.hint.color}
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        testID="login-password"
      />
      {login.error ? (
        <Text style={styles.error} testID="login-error">
          {login.error.message}
        </Text>
      ) : null}
      <Pressable
        accessibilityRole="button"
        style={[styles.button, login.isPending && styles.buttonDisabled]}
        disabled={login.isPending || !email || !password}
        onPress={() => login.mutate({ email, password })}
        testID="login-submit"
      >
        <Text style={styles.buttonText}>
          {login.isPending ? t("Signing in…") : t("Sign in")}
        </Text>
      </Pressable>
    </View>
  );
}

function BooksCard() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const books = useBooks();
  return (
    <View style={styles.card}>
      <Text style={styles.label}>{t("Books")}</Text>
      {books.isPending ? <Text>{t("Loading…")}</Text> : null}
      {books.error ? (
        <Text style={styles.error}>{books.error.message}</Text>
      ) : null}
      {books.data?.length === 0 ? (
        <Text style={styles.hint}>{t("No books yet.")}</Text>
      ) : null}
      {books.data?.map((b) => (
        <View key={b.id} style={styles.bookRow} testID={`book-${b.id}`}>
          <Text style={styles.bookName}>{b.name}</Text>
          <Text style={styles.bookRole}>{b.role ?? ""}</Text>
        </View>
      ))}
    </View>
  );
}

function Shell() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const { locale } = useI18n();
  const session = useSession();
  const logout = useLogout();
  const user = session.data ?? null;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>{t("PyMoney")}</Text>
      <SessionExpiredBanner />
      <LanguageBar />

      <View style={styles.card}>
        <Text style={styles.label}>{t("Locale")}:</Text>
        <Text style={styles.value} testID="locale">
          {locale}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>{t("Balance")}:</Text>
        <Text style={styles.money} testID="money">
          {formatAmount(sample, {
            locale: locale === "nl" ? "nl-NL" : "en-US",
            withCommodity: true,
          })}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.label}>{t("Session")}:</Text>
        {session.isPending ? <Text>{t("Loading…")}</Text> : null}
        {user ? (
          <View style={styles.sessionBox}>
            <Text style={styles.value} testID="session-email">
              {user.email}
            </Text>
            <Text style={styles.hint} testID="session-name">
              {user.display_name}
            </Text>
            <Pressable
              accessibilityRole="button"
              style={styles.button}
              onPress={() => logout.mutate()}
              testID="logout"
            >
              <Text style={styles.buttonText}>{t("Sign out")}</Text>
            </Pressable>
          </View>
        ) : session.isPending ? null : (
          <Text style={styles.hint} testID="session-anonymous">
            {t("Not signed in")}
          </Text>
        )}
      </View>

      {user ? <BooksCard /> : <LoginForm />}

      <Text style={styles.hint}>
        {t("Shell renders en + nl. Device floor iOS 16+ / Android 10+.")}
      </Text>
    </ScrollView>
  );
}

export default function Home() {
  return <Shell />;
}

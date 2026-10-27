/**
 * The signed-in home (E1.1: the session surface until E1.2's book shell).
 * Shows who is signed in, offers log out, and lists the books — all live API
 * reads through `useSession` / `useBooks`. The auth gate keeps this screen
 * reachable only with a session.
 */
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useBooks, useLogout, useSession, useT } from "@pymoney/core/hooks";

import { useUnauthorized } from "../src/api/unauthorized";
import { LanguageBar } from "../src/i18n/LanguageBar";
import { useThemedStyles, type Theme } from "../src/theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      gap: t.spacing.lg,
      backgroundColor: t.colors.surface,
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
    hint: {
      fontSize: 12,
      color: t.colors.muted,
    },
    error: {
      fontSize: 13,
      color: t.colors.negative,
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

function BooksCard() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const books = useBooks();
  return (
    <View style={styles.card}>
      <Text style={styles.label}>{t("Books")}</Text>
      {books.isPending ? <Text>{t("Loading…")}</Text> : null}
      {books.error ? <Text style={styles.error}>{books.error.message}</Text> : null}
      {books.data?.length === 0 ? (
        <Text style={styles.hint}>{t("No books yet.")}</Text>
      ) : null}
      {books.data?.map((b) => (
        <View key={b.id} style={styles.bookRow} testID={`book-${b.id}`}>
          <Text style={styles.bookName}>{b.name}</Text>
          <Text style={styles.bookRole}>{b.role ? t(b.role) : ""}</Text>
        </View>
      ))}
    </View>
  );
}

export default function Home() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const session = useSession();
  const logout = useLogout();
  const { consume } = useUnauthorized();
  const user = session.data ?? null;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <LanguageBar />
      <View style={styles.card}>
        <Text style={styles.label}>{t("Session")}</Text>
        {session.isPending ? <Text>{t("Loading…")}</Text> : null}
        {user ? (
          <View>
            <Text style={styles.value} testID="session-email">
              {user.email}
            </Text>
            <Text style={styles.hint} testID="session-name">
              {user.display_name}
            </Text>
            <Pressable
              accessibilityRole="button"
              style={[styles.button, logout.isPending && styles.buttonDisabled]}
              disabled={logout.isPending}
              // A logout whose own request dies with the token would raise the
              // expiry banner on the way out; dismiss it again.
              onPress={() => logout.mutate(undefined, { onSettled: () => consume() })}
              testID="logout"
            >
              <Text style={styles.buttonText}>{t("Log out")}</Text>
            </Pressable>
          </View>
        ) : null}
      </View>
      <BooksCard />
    </ScrollView>
  );
}

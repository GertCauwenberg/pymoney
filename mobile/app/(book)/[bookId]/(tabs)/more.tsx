/**
 * More tab (E1.2 shell): which book is open and how to change it, the
 * "Keep this book offline" pin for the current book, the language chips, and
 * the session with log out (moved here from the old E1.1 home surface). Sync
 * status and settings land here too (E5, E8).
 */
import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { useBook, useLogout, useSession, useT } from "@pymoney/core/hooks";

import { useBookId } from "../../../../src/book/BookScope";
import { useUnauthorized } from "../../../../src/api/unauthorized";
import { LanguageBar } from "../../../../src/i18n/LanguageBar";
import { useBookPrefs, useBookPrefsSnapshot } from "../../../../src/data/BookPrefsProvider";
import { useThemedStyles, type Theme } from "../../../../src/theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      gap: t.spacing.lg,
      backgroundColor: t.colors.surface,
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
    row: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      gap: t.spacing.sm,
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
  });
}

export default function MoreTab() {
  const t = useT();
  const router = useRouter();
  const styles = useThemedStyles(createStyles);
  const bookId = useBookId();
  const book = useBook(bookId);
  const session = useSession();
  const logout = useLogout();
  const prefs = useBookPrefs();
  const { offlinePinBookId } = useBookPrefsSnapshot();
  const { consume } = useUnauthorized();
  const user = session.data ?? null;

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View>
        <Text style={styles.label}>{t("Book")}</Text>
        <Text style={styles.value} testID="more-book-name">
          {book.data?.name ?? ""}
        </Text>
        {book.data?.role ? <Text style={styles.hint}>{t(book.data.role)}</Text> : null}
      </View>
      <Pressable
        accessibilityRole="button"
        onPress={() => router.push("/books")}
        testID="more-books"
      >
        <Text style={styles.label}>{t("Your books")}</Text>
      </Pressable>
      <View style={styles.row}>
        <Text style={styles.label}>{t("Keep this book offline")}</Text>
        <Switch
          accessibilityRole="switch"
          accessibilityLabel={t("Keep this book offline")}
          value={offlinePinBookId === bookId}
          onValueChange={(on) => void prefs.setOfflinePinBookId(on ? bookId : null)}
          testID="more-pin"
        />
      </View>
      <LanguageBar />
      <View>
        <Text style={styles.label}>{t("Session")}</Text>
        {session.isPending ? <Text style={styles.hint}>{t("Loading…")}</Text> : null}
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
    </ScrollView>
  );
}

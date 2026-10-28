/**
 * Book picker (E1.2): choose the book the tab shell shows, and mark the one
 * book to **keep offline** ("at most one" — a new pin replaces the old; the
 * mirror consequences of a pin change are E5.1's, not this screen's).
 * Opening a book is navigation plus recording the last-opened id; switching
 * never triggers a mirror re-pull. With no books at all, the empty state links
 * to onboarding on the web — book creation and registration stay web-only.
 */
import * as Linking from "expo-linking";
import { Pressable, ScrollView, StyleSheet, Switch, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { useBooks, useT } from "@pymoney/core/hooks";

import { WEB_BASE } from "../src/api/client";
import { useBookPrefs, useBookPrefsSnapshot } from "../src/data/BookPrefsProvider";
import { useThemedStyles, type Theme } from "../src/theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      gap: t.spacing.lg,
      backgroundColor: t.colors.surface,
    },
    hint: {
      fontSize: 14,
      color: t.colors.muted,
    },
    error: {
      fontSize: 13,
      color: t.colors.negative,
    },
    row: {
      borderWidth: 1,
      borderColor: t.colors.border,
      borderRadius: t.radius.md,
    },
    rowMain: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      padding: 14,
      gap: t.spacing.sm,
    },
    name: {
      fontSize: 16,
      fontWeight: "600",
      color: t.colors.text,
    },
    role: {
      fontSize: 14,
      color: t.colors.muted,
    },
    pinRow: {
      flexDirection: "row",
      justifyContent: "space-between",
      alignItems: "center",
      paddingHorizontal: 14,
      paddingVertical: 10,
      borderTopWidth: StyleSheet.hairlineWidth,
      borderTopColor: t.colors.border,
      gap: t.spacing.sm,
    },
    pinLabel: {
      flex: 1,
      fontSize: 14,
      color: t.colors.text,
    },
    link: {
      color: t.colors.accent,
      fontSize: 14,
      fontWeight: "600",
      paddingVertical: 8,
    },
  });
}

export default function BooksScreen() {
  const t = useT();
  const router = useRouter();
  const styles = useThemedStyles(createStyles);
  const books = useBooks();
  const prefs = useBookPrefs();
  const { offlinePinBookId } = useBookPrefsSnapshot();

  const openBook = (bookId: string) => {
    // Opening a book is navigation only — no mirror pull (E5.1 owns cache
    // policy). The book layout records the last-opened id.
    router.replace({ pathname: "/(book)/[bookId]", params: { bookId } });
  };

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {books.isPending ? <Text style={styles.hint}>{t("Loading your books…")}</Text> : null}
      {books.error ? <Text style={styles.error}>{books.error.message}</Text> : null}
      {books.data?.length === 0 ? (
        <View>
          <Text style={styles.hint}>{t("No books yet.")}</Text>
          <Pressable
            accessibilityRole="link"
            onPress={() => void Linking.openURL(WEB_BASE)}
            testID="books-onboarding"
          >
            <Text style={styles.link}>{t("Create a book on the web")}</Text>
          </Pressable>
        </View>
      ) : null}
      {books.data?.map((b) => (
        <View key={b.id} style={styles.row}>
          <Pressable
            accessibilityRole="button"
            style={styles.rowMain}
            onPress={() => openBook(b.id)}
            testID={`book-${b.id}`}
          >
            <Text style={styles.name}>{b.name}</Text>
            <Text style={styles.role}>{b.role ? t(b.role) : ""}</Text>
          </Pressable>
          <View style={styles.pinRow}>
            <Text style={styles.pinLabel}>{t("Keep this book offline")}</Text>
            <Switch
              accessibilityRole="switch"
              accessibilityLabel={t("Keep this book offline")}
              value={offlinePinBookId === b.id}
              onValueChange={(on) => void prefs.setOfflinePinBookId(on ? b.id : null)}
              testID={`book-pin-${b.id}`}
            />
          </View>
        </View>
      ))}
    </ScrollView>
  );
}

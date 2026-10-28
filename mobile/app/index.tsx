/**
 * The signed-in entry point (E1.2): a redirect, not a screen. It lands on the
 * last-opened book when the user can still see it, the sole book, or the
 * picker — `pickInitialBook` decides. Opening a book is navigation plus
 * recording `lastOpenedBookId` (done by the book layout); it never triggers a
 * mirror pull, which is E5.1's business.
 */
import { useEffect, useRef } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useRouter } from "expo-router";

import { useBooks } from "@pymoney/core/hooks";

import { pickInitialBook } from "../src/data/bookPrefs";
import { useBookPrefsSnapshot } from "../src/data/BookPrefsProvider";
import { useTheme, useThemedStyles, type Theme } from "../src/theme";

function createStyles(theme: Theme) {
  return StyleSheet.create({
    splash: {
      flex: 1,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: theme.colors.surface,
    },
  });
}

export default function Index() {
  const router = useRouter();
  const theme = useTheme();
  const styles = useThemedStyles(createStyles);
  const books = useBooks();
  const { lastOpenedBookId } = useBookPrefsSnapshot();
  const moved = useRef(false);

  useEffect(() => {
    if (moved.current || books.isPending) return;
    moved.current = true;
    const decision = pickInitialBook(books.data ?? [], lastOpenedBookId);
    if (decision.kind === "open") {
      router.replace({ pathname: "/(book)/[bookId]", params: { bookId: decision.bookId } });
    } else {
      router.replace("/books");
    }
  }, [books.isPending, books.data, lastOpenedBookId, router]);

  return (
    <View style={styles.splash}>
      <ActivityIndicator size="large" color={theme.colors.accent} />
    </View>
  );
}

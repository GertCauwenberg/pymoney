/**
 * Home tab (E1.2 shell): the book's name and the caller's role on it. The
 * dashboard itself (balances, recent transactions, budget card) is E2.1.
 * Showing the name here is also what proves a book switch reloads the tabs.
 */
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { useBook, useT } from "@pymoney/core/hooks";

import { useBookId } from "../../../../src/book/BookScope";
import { useThemedStyles, type Theme } from "../../../../src/theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      gap: t.spacing.sm,
      backgroundColor: t.colors.surface,
    },
    name: {
      fontSize: 24,
      fontWeight: "700",
      color: t.colors.text,
    },
    role: {
      fontSize: 14,
      color: t.colors.muted,
    },
    error: {
      fontSize: 13,
      color: t.colors.negative,
    },
  });
}

export default function HomeTab() {
  const t = useT();
  const styles = useThemedStyles(createStyles);
  const bookId = useBookId();
  const book = useBook(bookId);
  return (
    <ScrollView contentContainerStyle={styles.container}>
      <View>
        <Text style={styles.name} testID="home-book-name">
          {book.data?.name ?? ""}
        </Text>
        {book.data?.role ? <Text style={styles.role}>{t(book.data.role)}</Text> : null}
        {book.error ? <Text style={styles.error}>{book.error.message}</Text> : null}
      </View>
    </ScrollView>
  );
}

/**
 * The per-book shell (E1.2): everything under a book id. Entering this layout
 * is "opening the book" — it records `lastOpenedBookId` (relaunch UX only)
 * and nothing else: no mirror pull, no cache policy. Book-level detail routes
 * (transactions, accounts, attachments — see MOBILE.md D) join this Stack
 * later, over the tab bar.
 */
import { useEffect } from "react";
import { Stack, useLocalSearchParams } from "expo-router";

import { BookScope } from "../../../src/book/BookScope";
import { useBookPrefs } from "../../../src/data/BookPrefsProvider";

export default function BookLayout() {
  const { bookId } = useLocalSearchParams<{ bookId: string }>();
  const prefs = useBookPrefs();

  useEffect(() => {
    if (bookId) void prefs.setLastOpenedBookId(bookId);
  }, [bookId, prefs]);

  return (
    <BookScope bookId={bookId}>
      <Stack>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
      </Stack>
    </BookScope>
  );
}

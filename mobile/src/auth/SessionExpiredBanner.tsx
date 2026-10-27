/**
 * The mid-session 401 notice (M7): the token was forgotten, the app is back
 * at the login screen, and whatever was queued offline is still queued. Tap
 * to dismiss.
 */
import { Pressable, StyleSheet, Text } from "react-native";

import { useT } from "@pymoney/core/hooks";

import { useUnauthorized } from "../api/unauthorized";
import { useThemedStyles, type Theme } from "../theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    banner: {
      backgroundColor: t.colors.negative,
      borderRadius: t.radius.sm,
      padding: 12,
    },
    text: {
      color: t.colors.accentText,
      fontWeight: "600",
      textAlign: "center",
    },
  });
}

export function SessionExpiredBanner() {
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
      <Text style={styles.text}>{t("Session expired — sign in to sync")}</Text>
    </Pressable>
  );
}

/**
 * Language chips for every screen, including the signed-out ones (E0.4: nl
 * must be reachable before login). UI state only — the account's own locale is
 * a separate, server-side setting.
 */
import { Pressable, StyleSheet, Text, View } from "react-native";

import { LOCALES } from "@pymoney/core";
import { useI18n } from "@pymoney/core/hooks";

import { useThemedStyles, type Theme } from "../theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    row: {
      flexDirection: "row",
      gap: t.spacing.sm,
    },
    chip: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: t.radius.full,
      borderWidth: 1,
      borderColor: t.colors.accent,
    },
    chipOn: {
      backgroundColor: t.colors.accent,
    },
    chipText: {
      color: t.colors.accent,
      fontWeight: "600",
    },
    chipTextOn: {
      color: t.colors.accentText,
    },
  });
}

export function LanguageBar() {
  const styles = useThemedStyles(createStyles);
  const { locale, setLocale } = useI18n();
  return (
    <View style={styles.row} accessibilityRole="tablist">
      {LOCALES.map((l) => {
        const selected = l.code === locale;
        return (
          <Pressable
            key={l.code}
            onPress={() => setLocale(l.code)}
            accessibilityRole="tab"
            accessibilityState={{ selected }}
            style={[styles.chip, selected && styles.chipOn]}
            testID={`lang-${l.code}`}
          >
            <Text style={[styles.chipText, selected && styles.chipTextOn]}>{l.name}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/**
 * The five-slot tab shell (E1.2): Home · Search · Add (the primary slot) ·
 * Spending · More. Native headers and the native tab bar carry the safe-area
 * insets on both platforms; screen bodies must not add their own. Each tab is
 * one screen for now — a tab that grows a second screen turns its route file
 * into a directory with its own Stack.
 */
import { Tabs } from "expo-router";

import { useT } from "@pymoney/core/hooks";

import { useTheme } from "../../../../src/theme";

export default function TabsLayout() {
  const t = useT();
  const theme = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.accent },
        headerTintColor: theme.colors.accentText,
        tabBarActiveTintColor: theme.colors.accent,
        tabBarInactiveTintColor: theme.colors.muted,
      }}
    >
      <Tabs.Screen name="index" options={{ title: t("Home") }} />
      <Tabs.Screen name="search" options={{ title: t("Search") }} />
      <Tabs.Screen name="add" options={{ title: t("Add") }} />
      <Tabs.Screen name="spending" options={{ title: t("Spending") }} />
      <Tabs.Screen name="more" options={{ title: t("More") }} />
    </Tabs>
  );
}

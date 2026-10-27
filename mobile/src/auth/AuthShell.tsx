/**
 * Chrome and form primitives shared by the signed-out screens (E1.1): one
 * scrollable card with the language bar on top, labelled fields, the primary
 * button, verbatim server errors ("Errors are the server's") and text links.
 */
import type { ReactNode } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInputProps,
} from "react-native";

import { LanguageBar } from "../i18n/LanguageBar";
import { SessionExpiredBanner } from "./SessionExpiredBanner";
import { useThemedStyles, type Theme } from "../theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    container: {
      padding: t.spacing.xl,
      gap: t.spacing.lg,
      backgroundColor: t.colors.surface,
    },
    title: {
      fontSize: 24,
      fontWeight: "700",
      color: t.colors.text,
    },
    blurb: {
      fontSize: 14,
      color: t.colors.muted,
    },
    field: {
      gap: t.spacing.xs,
    },
    label: {
      fontSize: 14,
      color: t.colors.text,
      fontWeight: "600",
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
    link: {
      color: t.colors.accent,
      fontSize: 14,
      fontWeight: "600",
      paddingVertical: 8,
    },
    errorBox: {
      borderWidth: 1,
      borderColor: t.colors.negative,
      borderRadius: t.radius.sm,
      padding: 12,
      gap: 4,
    },
    errorTitle: {
      fontSize: 14,
      fontWeight: "700",
      color: t.colors.negative,
    },
    errorMessage: {
      fontSize: 14,
      color: t.colors.negative,
    },
  });
}

/** Scrollable single-card screen shell for login and forgot-password. */
export function AuthShell({
  title,
  blurb,
  banner = true,
  children,
}: {
  title: string;
  blurb?: string;
  banner?: boolean;
  children: ReactNode;
}) {
  const styles = useThemedStyles(createStyles);
  return (
    <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
      {banner ? <SessionExpiredBanner /> : null}
      <LanguageBar />
      <Text style={styles.title}>{title}</Text>
      {blurb ? <Text style={styles.blurb}>{blurb}</Text> : null}
      {children}
    </ScrollView>
  );
}

export function AuthField({
  label,
  ...input
}: TextInputProps & { label: string }) {
  const styles = useThemedStyles(createStyles);
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        autoCapitalize="none"
        placeholderTextColor={styles.blurb.color}
        {...input}
      />
    </View>
  );
}

/** The one filled button on an auth screen; greyed while disabled. */
export function AuthButton({
  label,
  pendingLabel,
  pending,
  disabled,
  onPress,
  testID,
}: {
  label: string;
  pendingLabel?: string;
  pending?: boolean;
  disabled?: boolean;
  onPress: () => void;
  testID?: string;
}) {
  const styles = useThemedStyles(createStyles);
  return (
    <Pressable
      accessibilityRole="button"
      style={[styles.button, disabled && styles.buttonDisabled]}
      disabled={disabled}
      onPress={onPress}
      testID={testID}
    >
      <Text style={styles.buttonText}>{pending && pendingLabel ? pendingLabel : label}</Text>
    </Pressable>
  );
}

/** Server errors verbatim, under a translated title. */
export function AuthError({ title, message }: { title: string; message: string }) {
  const styles = useThemedStyles(createStyles);
  return (
    <View style={styles.errorBox} accessibilityRole="alert" testID="auth-error">
      <Text style={styles.errorTitle}>{title}</Text>
      <Text style={styles.errorMessage} testID="auth-error-message">
        {message}
      </Text>
    </View>
  );
}

export function AuthLink({
  label,
  onPress,
  testID,
}: {
  label: string;
  onPress: () => void;
  testID?: string;
}) {
  const styles = useThemedStyles(createStyles);
  return (
    <Pressable accessibilityRole="link" onPress={onPress} testID={testID}>
      <Text style={styles.link}>{label}</Text>
    </Pressable>
  );
}

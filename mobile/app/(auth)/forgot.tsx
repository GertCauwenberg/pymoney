/**
 * Forgot password (E1.1): the minimal request-only flow — `POST
 * /auth/forgot-password`, then the same answer whatever the address is
 * (non-disclosure), exactly like the web page. Choosing a new password is the
 * emailed link's job (web).
 */
import { useState } from "react";
import { StyleSheet, Text } from "react-native";
import { useRouter } from "expo-router";

import { useForgotPassword, useT } from "@pymoney/core/hooks";

import { AuthButton, AuthError, AuthField, AuthLink, AuthShell } from "../../src/auth/AuthShell";
import { useThemedStyles, type Theme } from "../../src/theme";

function createStyles(t: Theme) {
  return StyleSheet.create({
    sent: {
      fontSize: 16,
      color: t.colors.text,
    },
    email: {
      fontWeight: "700",
    },
  });
}

export default function ForgotScreen() {
  const t = useT();
  const router = useRouter();
  const styles = useThemedStyles(createStyles);
  const forgot = useForgotPassword();
  const [email, setEmail] = useState("");

  if (forgot.isSuccess) {
    return (
      <AuthShell title={t("Check your email")} banner={false}>
        <Text style={styles.sent}>
          {t("If")}{" "}
          <Text style={styles.email} testID="forgot-sent-email">
            {forgot.data.email}
          </Text>{" "}
          {t("belongs to an activated account, a link to choose a new password is on its way. It is valid for a couple of hours.")}
        </Text>
        <AuthLink
          label={t("Back to log in")}
          onPress={() => router.replace("/(auth)/login")}
          testID="forgot-back"
        />
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title={t("Forgotten your password?")}
      blurb={t("Enter the address you registered with and we will email you a link to choose a new one.")}
      banner={false}
    >
      {forgot.error ? (
        <AuthError title={t("Could not send the link")} message={forgot.error.message} />
      ) : null}
      <AuthField
        label={t("Email")}
        autoComplete="email"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
        testID="forgot-email"
      />
      <AuthButton
        label={t("Email me a link")}
        disabled={forgot.isPending || !email.trim()}
        onPress={() => forgot.mutate(email.trim())}
        testID="forgot-submit"
      />
      <AuthLink
        label={t("Back to log in")}
        onPress={() => router.replace("/(auth)/login")}
        testID="forgot-back"
      />
    </AuthShell>
  );
}

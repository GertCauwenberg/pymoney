/**
 * Login (E1.1): email/password via `useLogin`, the server's error message
 * verbatim, and a link to the forgot-password request. Registering and
 * activation stay web-only. The bearer token lands in `SecureAuthStore`
 * inside `ApiClient.remember()`.
 */
import { useState } from "react";
import { useRouter } from "expo-router";

import { useLogin, useT } from "@pymoney/core/hooks";

import { useUnauthorized } from "../../src/api/unauthorized";
import { AuthButton, AuthError, AuthField, AuthLink, AuthShell } from "../../src/auth/AuthShell";

export default function LoginScreen() {
  const t = useT();
  const router = useRouter();
  const login = useLogin();
  const { consume } = useUnauthorized();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = () => {
    // A fresh attempt acknowledges the expiry notice; the form's own error
    // answers this attempt.
    consume();
    login.mutate({ email, password });
  };

  return (
    <AuthShell title={t("Log in")}>
      {login.error ? (
        <AuthError title={t("Could not log in")} message={login.error.message} />
      ) : null}
      <AuthField
        label={t("Email")}
        autoComplete="email"
        keyboardType="email-address"
        value={email}
        onChangeText={setEmail}
        testID="login-email"
      />
      <AuthField
        label={t("Password")}
        autoComplete="password"
        secureTextEntry
        value={password}
        onChangeText={setPassword}
        testID="login-password"
      />
      <AuthButton
        label={t("Log in")}
        pendingLabel={t("Logging in…")}
        pending={login.isPending}
        disabled={login.isPending || !email || !password}
        onPress={submit}
        testID="login-submit"
      />
      <AuthLink
        label={t("Forgotten your password?")}
        onPress={() => router.push("/(auth)/forgot")}
        testID="login-forgot-link"
      />
    </AuthShell>
  );
}

import { useT } from "@pymoney/core/hooks";
import { useActivate, useForgotPassword, useResetPassword } from "@pymoney/core/hooks";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Field } from "../components/fields";

/** Ask for a reset link. The answer is the same whether the address exists. */
export function ForgotPasswordPage() {
  const t = useT();
  const location = useLocation();
  const forgot = useForgotPassword();
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? "");

  return (
    <main className="page page--narrow">
      <h1 className="brand">{t("PyMoney")}</h1>
      {forgot.isSuccess ? (
        <div className="card">
          <h2>{t("Check your email")}</h2>
          <p>
            {t("If")} <strong>{forgot.data.email}</strong> {t("belongs to an activated account, a link to choose a new password is on its way. It is valid for a couple of hours.")}
          </p>
          <p className="muted">
            <Link to="/login">{t("Back to log in")}</Link>
          </p>
        </div>
      ) : (
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            forgot.mutate(email.trim());
          }}
        >
          <h2>{t("Forgotten your password?")}</h2>
          <p className="muted">{t("Enter the address you registered with and we will email you a link to choose a new one.")}</p>
          {forgot.error && <ErrorBanner error={forgot.error} title={t("Could not send the link")} />}
          <Field label={t("Email")}>
            <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className="btn btn--primary" disabled={forgot.isPending || !email.trim()}>
              {forgot.isPending ? "…" : t("Email me a link")}
            </button>
            <Link to="/login" className="btn btn--link">
              {t("Back to log in")}
            </Link>
          </div>
        </form>
      )}
    </main>
  );
}

/** The page the reset link opens: choose a new password. */
export function ResetPasswordPage() {
  const t = useT();
  const [search] = useSearchParams();
  const token = search.get("token") ?? "";
  const reset = useResetPassword();
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const mismatch = again !== "" && again !== password;

  if (reset.isSuccess) return <Navigate to="/books" replace />;

  return (
    <main className="page page--narrow">
      <h1 className="brand">{t("PyMoney")}</h1>
      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          if (!mismatch) reset.mutate({ token, password });
        }}
      >
        <h2>{t("Choose a new password")}</h2>
        {!token && (
          <div className="banner banner--warning" role="alert">
            {t("This page needs the link from the email.")} <Link to="/forgot-password">{t("Ask for a new one.")}</Link>
          </div>
        )}
        {reset.error && (
          <ErrorBanner error={reset.error} title={t("Could not reset the password")} />
        )}
        {reset.error && (reset.error as { status?: number }).status === 410 && (
          <p className="muted">
            <Link to="/forgot-password">{t("Ask for a new link.")}</Link>
          </p>
        )}
        <Field label={t("New password")} hint={t("At least 12 characters. Every other session of your account will be logged out.")}>
          <input type="password" required minLength={12} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label={t("Repeat it")} hint={mismatch ? t("The two do not match.") : undefined}>
          <input type="password" required autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        <div className="actions">
          <button type="submit" className="btn btn--primary" disabled={reset.isPending || !token || mismatch || password.length < 12}>
            {reset.isPending ? "…" : t("Set the password and log in")}
          </button>
        </div>
      </form>
    </main>
  );
}

/** The page the activation link opens: nothing to fill in, it just happens. */
export function ActivatePage() {
  const t = useT();
  const [search] = useSearchParams();
  const token = search.get("token") ?? "";
  const activate = useActivate();
  // Exactly once: the token is single use, and React (StrictMode, re-renders)
  // runs effects more than once.
  const started = useRef(false);
  const { mutate } = activate;
  useEffect(() => {
    if (token && !started.current) {
      started.current = true;
      mutate(token);
    }
  }, [token, mutate]);

  if (activate.isSuccess) return <Navigate to="/books" replace />;

  return (
    <main className="page page--narrow">
      <h1 className="brand">{t("PyMoney")}</h1>
      <div className="card">
        <h2>{t("Activating your account")}</h2>
        {!token && (
          <div className="banner banner--warning" role="alert">
            {t("This page needs the link from the email.")}
          </div>
        )}
        {activate.isPending && <p className="muted">{t("One moment…")}</p>}
        {activate.error && (
          <>
            <ErrorBanner error={activate.error} title={t("Could not activate")} />
            <p className="muted">
              {t("Links expire and work only once.")} <Link to="/login">{t("Register again")}</Link> {t("and a new one is sent, or log in if you already activated.")}
            </p>
          </>
        )}
      </div>
    </main>
  );
}

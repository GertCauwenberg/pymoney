import { isPending } from "@pymoney/core";
import type { Pending } from "@pymoney/core";
import { useLogin, useRegister, useResendActivation, useSession } from "@pymoney/core/hooks";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Field } from "../components/fields";

export function LoginPage() {
  const session = useSession();
  const login = useLogin();
  const register = useRegister();
  const resend = useResendActivation();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/books";
  if (session.data) return <Navigate to={from} replace />;

  const busy = login.isPending || register.isPending;
  const error = mode === "login" ? login.error : register.error;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mode === "login") login.mutate({ email, password });
    else
      register.mutate(
        { email, password, display_name: displayName },
        { onSuccess: (result) => setPending(isPending(result) ? result : null) },
      );
  };

  if (pending) {
    return (
      <main className="page page--narrow">
        <h1 className="brand">PyMoney</h1>
        <div className="card">
          <h2>Check your email</h2>
          <p>
            We sent an activation link to <strong>{pending.email}</strong>. The account works once you have
            followed it; until then it cannot log in, and it is deleted automatically if the link is never used.
          </p>
          {resend.isSuccess && <p className="muted">Sent again.</p>}
          {resend.error && <ErrorBanner error={resend.error} title="Could not resend" />}
          <div className="actions">
            <button type="button" className="btn" disabled={resend.isPending} onClick={() => resend.mutate(pending.email)}>
              Send the link again
            </button>
            <button
              type="button"
              className="btn btn--link"
              onClick={() => {
                setPending(null);
                setMode("login");
                setPassword("");
              }}
            >
              Back to log in
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="page page--narrow">
      <h1 className="brand">PyMoney</h1>
      <form className="card" onSubmit={submit}>
        <h2>{mode === "login" ? "Log in" : "Create an account"}</h2>
        {error && <ErrorBanner error={error} title={mode === "login" ? "Could not log in" : "Could not register"} />}
        <Field label="Email">
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" hint={mode === "register" ? "At least 12 characters." : undefined}>
          <input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={mode === "register" ? 12 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        {mode === "register" && (
          <Field label="Display name">
            <input type="text" autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </Field>
        )}
        <div className="actions">
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? "…" : mode === "login" ? "Log in" : "Register"}
          </button>
          <button
            type="button"
            className="btn btn--link"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "New here? Create an account" : "Have an account? Log in"}
          </button>
        </div>
        {mode === "login" && (
          <p className="muted">
            <Link to="/forgot-password" state={{ email }}>
              Forgotten your password?
            </Link>
          </p>
        )}
      </form>
    </main>
  );
}

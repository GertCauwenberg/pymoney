import { useLogin, useRegister, useSession } from "@pymoney/core/hooks";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Field } from "../components/fields";

export function LoginPage() {
  const session = useSession();
  const login = useLogin();
  const register = useRegister();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");

  const from = (location.state as { from?: string } | null)?.from ?? "/books";
  if (session.data) return <Navigate to={from} replace />;

  const pending = login.isPending || register.isPending;
  const error = mode === "login" ? login.error : register.error;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mode === "login") login.mutate({ email, password });
    else register.mutate({ email, password, display_name: displayName });
  };

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
          <button type="submit" className="btn btn--primary" disabled={pending}>
            {pending ? "…" : mode === "login" ? "Log in" : "Register"}
          </button>
          <button
            type="button"
            className="btn btn--link"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
          >
            {mode === "login" ? "New here? Create an account" : "Have an account? Log in"}
          </button>
        </div>
      </form>
    </main>
  );
}

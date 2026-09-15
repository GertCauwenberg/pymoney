import { useT } from "@pymoney/core/hooks";
import type { Book } from "@pymoney/core";
import { useBooks, useDeleteAccount, useLogout, useSession } from "@pymoney/core/hooks";
import { useState, type SubmitEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { Field } from "../components/fields";
import { Loading } from "../components/Loading";
import { ImportBookForm } from "./ImportBookForm";
import { NewBookForm } from "./NewBookForm";
import { Logo } from "../components/Logo";

export function BooksPage() {
  const t = useT();
  const books = useBooks();
  const session = useSession();
  const logout = useLogout();
  const location = useLocation();
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  if (books.isPending) return <Loading label={t("Loading your books…")} />;
  if (books.isError) {
    return (
      <main className="page page--narrow">
        <ErrorBanner error={books.error} onRetry={() => void books.refetch()} />
      </main>
    );
  }

  // One book is the common case for a household; do not make them click --
  // unless they came here on purpose (the brand link passes `pick`), because
  // this is also where a second book is created.
  const pick = (location.state as { pick?: boolean } | null)?.pick === true;
  if (books.data.length === 1 && !pick && !creating && !importing) {
    return <Navigate to={`/books/${books.data[0]!.id}`} replace />;
  }

  const none = books.data.length === 0;

  return (
    <main className="page page--narrow">
      <header className="topbar">
        <h1 className="brand"><Logo />{t("PyMoney")}</h1>
        <div className="topbar__user">
          <LanguageSwitch />
          {session.data?.display_name || session.data?.email}
          <button type="button" className="btn btn--small" onClick={() => logout.mutate()}>
            {t("Log out")}
          </button>
        </div>
      </header>

      {none ? (
        <>
          <h2>{t("Welcome")}</h2>
          <p>
            {t("You are logged in as")} <strong>{session.data?.email}</strong> {t("but have no books yet. Create one below — or, if someone else already keeps the books, ask them to share with this email address under")} <em>{t("Sharing")}</em> {t("in their book; it will then appear here.")}
          </p>
          <NewBookForm first />
          <p className="muted">
            Coming from KMyMoney?{" "}
            <button type="button" className="btn btn--link" onClick={() => setImporting(true)}>
              {t("Import your .kmy file")}
            </button>{" "}
            and the book is made for you.
          </p>
        </>
      ) : (
        <>
          <div className="page-heading">
            <h2>{t("Your books")}</h2>
            <span className="actions actions--inline">
              {!creating && (
                <button type="button" className="btn" onClick={() => setCreating(true)}>
                  {t("New book")}
                </button>
              )}
              {!importing && (
                <button type="button" className="btn" onClick={() => setImporting(true)}>
                  {t("Import from KMyMoney")}
                </button>
              )}
            </span>
          </div>
          {creating && <NewBookForm onDone={() => setCreating(false)} />}
          <ul className="cards">
            {books.data.map((b) => (
              <li key={b.id} className="card card--link">
                <table width="100%">
                  <tbody>
                    <tr>
                      <td rowSpan={2}>
                        <Link to={`/books/${b.id}`}>
                          <strong>{b.name}</strong>
                          <span className="muted">
                            {b.role ?? "viewer"} · revision {b.revision}
                          </span>
                        </Link>
                      </td>
                      <td>
                        {b.role == 'owner' && (<Link to={`/books/${b.id}/sharing`}>{t("Sharing")}</Link>)}
                      </td></tr><tr><td>
                        {b.role == 'owner' && (<Link to={`/books/${b.id}/delete`}>{t("Delete")}</Link>)}
                      </td></tr></tbody></table>
              </li>
            ))}
          </ul>
        </>
      )}
      {/* Outside the two branches above: a successful import turns "no books"
          into "your books", and the form -- with its report -- must survive that. */}
      {importing && <ImportBookForm onDone={() => setImporting(false)} />}
      <DeleteAccount books={books.data} />
    </main>
  );
}

/**
 * Leaving. The server refuses while the user owns a book -- a book without an
 * owner can never be administered again -- so the page says so up front and
 * points at what to do about it.
 */
function DeleteAccount({ books }: { books: readonly Book[] }) {
  const t = useT();
  const del = useDeleteAccount();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const owned = books.filter((b) => b.role === "owner");

  const submit = (e: SubmitEvent) => {
    e.preventDefault();
    del.mutate(password);
  };

  return (
    <section className="card danger account-deletion" aria-labelledby="delete-account-heading">
      <h2 id="delete-account-heading">{t("Delete my account")}</h2>
      {owned.length > 0 ? (
        <p className="muted">
          You own {owned.length === 1 ? "a book" : `${owned.length} books`} ({owned.map((b) => b.name).join(", ")}). Delete{" "}
          {owned.length === 1 ? "it" : "them"} or make someone else the owner under <em>{t("Sharing")}</em> {t("first; an account that owns a book cannot be deleted.")}
        </p>
      ) : (
        <p className="muted">
          {t("Removes your login and your access to the books shared with you. The books themselves stay with their owners. There is no undo.")}
        </p>
      )}
      {!open ? (
        <button type="button" className="btn btn--danger" disabled={owned.length > 0} onClick={() => setOpen(true)}>
          {t("Delete my account…")}
        </button>
      ) : (
        <form onSubmit={submit}>
          {del.error && <ErrorBanner error={del.error} title={t("Could not delete the account")} />}
          <Field label={t("Your password, to confirm")}>
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className="btn btn--danger" disabled={del.isPending || !password}>
              {del.isPending ? "…" : t("Delete my account, irreversibly")}
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)} disabled={del.isPending}>
              {t("Keep it")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

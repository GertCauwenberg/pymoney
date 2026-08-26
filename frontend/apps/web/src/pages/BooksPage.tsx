import { useBooks, useLogout, useSession } from "@pymoney/core/hooks";
import { useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { NewBookForm } from "./NewBookForm";

export function BooksPage() {
  const books = useBooks();
  const session = useSession();
  const logout = useLogout();
  const location = useLocation();
  const [creating, setCreating] = useState(false);

  if (books.isPending) return <Loading label="Loading your books…" />;
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
  if (books.data.length === 1 && !pick && !creating) {
    return <Navigate to={`/books/${books.data[0]!.id}`} replace />;
  }

  const none = books.data.length === 0;

  return (
    <main className="page page--narrow">
      <header className="topbar">
        <h1 className="brand">PyMoney</h1>
        <div className="topbar__user">
          {session.data?.display_name || session.data?.email}
          <button type="button" className="btn btn--small" onClick={() => logout.mutate()}>
            Log out
          </button>
        </div>
      </header>

      {none ? (
        <>
          <h2>Welcome</h2>
          <p>
            You are logged in as <strong>{session.data?.email}</strong> but have no books yet. Create one
            below — or, if someone else already keeps the books, ask them to share with this email address
            under <em>Sharing</em> in their book; it will then appear here.
          </p>
          <NewBookForm first />
        </>
      ) : (
        <>
          <div className="page-heading">
            <h2>Your books</h2>
            {!creating && (
              <button type="button" className="btn" onClick={() => setCreating(true)}>
                New book
              </button>
            )}
          </div>
          {creating && <NewBookForm onDone={() => setCreating(false)} />}
          <ul className="cards">
            {books.data.map((b) => (
              <li key={b.id} className="card card--link">
                <Link to={`/books/${b.id}`}>
                  <strong>{b.name}</strong>
                  <span className="muted">
                    {b.role ?? "viewer"} · revision {b.revision}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}

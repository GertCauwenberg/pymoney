import { useBooks, useLogout, useSession } from "@pymoney/core/hooks";
import { Link, Navigate } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";

export function BooksPage() {
  const books = useBooks();
  const session = useSession();
  const logout = useLogout();

  if (books.isPending) return <Loading label="Loading your books…" />;
  if (books.isError) {
    return (
      <main className="page page--narrow">
        <ErrorBanner error={books.error} onRetry={() => void books.refetch()} />
      </main>
    );
  }
  // One book is the common case for a household; do not make them click.
  if (books.data.length === 1) return <Navigate to={`/books/${books.data[0]!.id}`} replace />;

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
      <h2>Your books</h2>
      {books.data.length === 0 ? (
        <p className="empty">
          You have no books yet. Create one through the API (<code>POST /books</code>) or ask an owner to grant you access.
        </p>
      ) : (
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
      )}
    </main>
  );
}

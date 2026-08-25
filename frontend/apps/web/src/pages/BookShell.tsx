import { useBook, useLogout, useSession } from "@pymoney/core/hooks";
import { NavLink, Outlet, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";

export interface BookContext {
  bookId: string;
  baseCurrencyId: string | null;
}

/** Everything under /books/:bookId: header, report navigation, the report. */
export function BookShell() {
  const { bookId = "" } = useParams();
  const book = useBook(bookId);
  const session = useSession();
  const logout = useLogout();

  if (book.isPending) return <Loading label="Opening the book…" />;
  if (book.isError) {
    return (
      <main className="page page--narrow">
        <ErrorBanner error={book.error} onRetry={() => void book.refetch()} />
        <p>
          <NavLink to="/books">Back to your books</NavLink>
        </p>
      </main>
    );
  }

  const context: BookContext = { bookId, baseCurrencyId: book.data.base_currency_id ?? null };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__left">
          <NavLink to="/books" className="brand">
            PyMoney
          </NavLink>
          <span className="topbar__book" title={`revision ${book.data.revision}`}>
            {book.data.name}
          </span>
        </div>
        <nav className="tabs" aria-label="Reports">
          <NavLink to="reports/balance-sheet">Balance sheet</NavLink>
          <NavLink to="reports/income-statement">Income statement</NavLink>
          <NavLink to="reports/holdings">Holdings</NavLink>
        </nav>
        <div className="topbar__user">
          <span className="muted">{session.data?.display_name || session.data?.email}</span>
          <button type="button" className="btn btn--small" onClick={() => logout.mutate()} disabled={logout.isPending}>
            Log out
          </button>
        </div>
      </header>
      <main className="page">
        <Outlet context={context} />
      </main>
    </div>
  );
}

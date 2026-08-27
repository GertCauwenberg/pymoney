import { useBook, useLogout, useSession } from "@pymoney/core/hooks";
import { NavLink, Outlet, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";

export interface BookContext {
  bookId: string;
  baseCurrencyId: string | null;
  /** The caller's role on this book: viewer, editor or owner. */
  role: string | null;
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

  const context: BookContext = {
    bookId,
    baseCurrencyId: book.data.base_currency_id ?? null,
    role: book.data.role ?? null,
  };

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__left">
          <NavLink to="/books" state={{ pick: true }} className="brand" title="Your books">
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
          <NavLink to="accounts" end className="tabs__aside">
            Accounts
          </NavLink>
          <NavLink to="commodities" className="tabs__aside">
            Commodities
          </NavLink>
          {book.data.role === "owner" && (
            <NavLink to="sharing" className="tabs__aside">
              Sharing
            </NavLink>
          )}
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

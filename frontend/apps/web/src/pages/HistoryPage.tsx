import { useT } from "@pymoney/core/hooks";
import { useAccounts, useHistory } from "@pymoney/core/hooks";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { HistoryTable } from "../components/HistoryTable";
import { Loading } from "../components/Loading";
import { useBookContext } from "../reports/useReportParams";

/** The book's change log, as people read it: who did what, newest first. */
export function HistoryPage() {
  const t = useT();
  const { bookId } = useBookContext();
  const [limit, setLimit] = useState(100);
  const history = useHistory(bookId, { limit });
  const accounts = useAccounts(bookId, { include_closed: true });
  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../../reports/balance-sheet">{t("Reports")}</Link> › {t("Settings")} › {t("History")}
          </p>
          <h1>{t("History")}</h1>
          <p className="muted">{t("Every change to this book, who made it and when. Each command is one revision.")}</p>
        </div>
      </header>
      {history.isPending && <Loading />}
      {history.isError && <ErrorBanner error={history.error} onRetry={() => void history.refetch()} />}
      {history.data && (
        <>
          <HistoryTable changes={history.data} accounts={accountsById} />
          {history.data.length >= limit && (
            <div className="load-more">
              <button type="button" className="btn" onClick={() => setLimit(limit + 200)}>
                {t("Show older changes")}
              </button>
            </div>
          )}
        </>
      )}
    </article>
  );
}

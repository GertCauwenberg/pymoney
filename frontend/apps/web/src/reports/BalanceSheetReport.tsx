import { rowsToCsv, sectionRows, today } from "@pymoney/core";
import type { BalanceSheetQuery } from "@pymoney/core";
import { useAccounts, useBalanceSheet, useCommodities } from "@pymoney/core/hooks";
import { useMemo } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { ReportActions } from "../components/ReportActions";
import { ReportTable } from "../components/ReportTable";
import { ValuationPanel } from "../components/ValuationPanel";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = {
  as_of: "",
  target: "",
  closed: false,
  strict: false,
};

export function BalanceSheetReport() {
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const asOf = params.as_of || today();

  const query = useMemo<BalanceSheetQuery>(
    () => ({
      as_of: asOf,
      ...(params.target ? { target_id: params.target } : {}),
      include_closed: params.closed,
      strict: params.strict,
    }),
    [asOf, params.target, params.closed, params.strict],
  );

  const report = useBalanceSheet(bookId, query);
  const accounts = useAccounts(bookId, { include_closed: true });
  const currencies = useCommodities(bookId, { kind: "CURRENCY" });

  const sections = useMemo(
    () => (report.data && accounts.data ? sectionRows(report.data.rows, accounts.data, ["ASSET", "LIABILITY", "EQUITY"]) : []),
    [report.data, accounts.data],
  );

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>Balance sheet</h1>
          <p className="muted">
            As of {asOf}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions
          csv={report.data ? () => rowsToCsv(report.data.rows) : null}
          filename={`balance-sheet-${asOf}.csv`}
        />
      </header>

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label="As of" value={asOf} onChange={(as_of) => update({ as_of })} />
        <CurrencyField
          currencies={currencies.data}
          value={params.target}
          baseCurrencyId={baseCurrencyId}
          onChange={(target) => update({ target })}
        />
        <div className="toolbar__toggles">
          <Toggle label="Include closed accounts" checked={params.closed} onChange={(closed) => update({ closed })} />
          <Toggle
            label="Fail if anything cannot be valued"
            hint="Off: list it below and leave it out of the totals."
            checked={params.strict}
            onChange={(strict) => update({ strict })}
          />
        </div>
      </form>

      {report.isError && <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />}
      {accounts.isError && <ErrorBanner error={accounts.error} onRetry={() => void accounts.refetch()} />}
      {report.isPending && !report.isError && <Loading />}

      {report.data && (
        <>
          <ReportTable
            sections={sections}
            totals={[
              { label: "Total assets", amount: report.data.assets },
              { label: "Total liabilities", amount: report.data.liabilities_owed },
              { label: "Net worth", amount: report.data.net_worth, emphasis: true },
            ]}
          />
          <ValuationPanel conversions={report.data.conversions} unpriced={report.data.unpriced} target={report.data.target} />
        </>
      )}
    </article>
  );
}

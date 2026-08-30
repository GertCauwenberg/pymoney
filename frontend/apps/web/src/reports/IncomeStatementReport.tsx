import { useT } from "@pymoney/core/hooks";
import { PERIOD_PRESETS, formatRatePercent, periodFor, presetOf, rowsToCsv, sectionRows } from "@pymoney/core";
import type { IncomeStatementQuery } from "@pymoney/core";
import { useAccounts, useCommodities, useIncomeStatement } from "@pymoney/core/hooks";
import { useMemo } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { ReportActions } from "../components/ReportActions";
import { ReportTable } from "../components/ReportTable";
import { ValuationPanel } from "../components/ValuationPanel";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = {
  start: "",
  end: "",
  target: "",
  closed: false,
  strict: false,
};

export function IncomeStatementReport() {
  const t = useT();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const fallback = periodFor("year-to-date");
  const period = { start: params.start || fallback.start, end: params.end || fallback.end };
  const activePreset = presetOf(period);

  const query = useMemo<IncomeStatementQuery>(
    () => ({
      start: period.start,
      end: period.end,
      ...(params.target ? { target_id: params.target } : {}),
      include_closed: params.closed,
      strict: params.strict,
    }),
    [period.start, period.end, params.target, params.closed, params.strict],
  );

  const report = useIncomeStatement(bookId, query, period.start <= period.end);
  const accounts = useAccounts(bookId, { include_closed: true });
  const currencies = useCommodities(bookId, { kind: "CURRENCY" });

  const sections = useMemo(
    () => (report.data && accounts.data ? sectionRows(report.data.rows, accounts.data, ["INCOME", "EXPENSE"]) : []),
    [report.data, accounts.data],
  );

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Income statement")}</h1>
          <p className="muted">
            {period.start} to {period.end}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions
          csv={report.data ? () => rowsToCsv(report.data.rows) : null}
          filename={`income-statement-${period.start}-${period.end}.csv`}
        />
      </header>

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <div className="presets" role="group" aria-label={t("Period")}>
          {PERIOD_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`chip${activePreset === p.id ? " chip--active" : ""}`}
              aria-pressed={activePreset === p.id}
              onClick={() => update(periodFor(p.id))}
            >
              {t(p.label)}
            </button>
          ))}
        </div>
        <DateField label={t("From")} value={period.start} max={period.end} onChange={(start) => update({ start })} />
        <DateField label={t("To")} value={period.end} min={period.start} onChange={(end) => update({ end })} />
        <CurrencyField
          currencies={currencies.data}
          value={params.target}
          baseCurrencyId={baseCurrencyId}
          onChange={(target) => update({ target })}
        />
        <div className="toolbar__toggles">
          <Toggle label={t("Include closed accounts")} checked={params.closed} onChange={(closed) => update({ closed })} />
          <Toggle
            label={t("Fail if anything cannot be valued")}
            hint={t("Off: list it below and leave it out of the totals.")}
            checked={params.strict}
            onChange={(strict) => update({ strict })}
          />
        </div>
      </form>

      {period.start > period.end && (
        <div className="banner banner--warning" role="alert">
          {t("The period ends before it starts.")}
        </div>
      )}
      {report.isError && <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />}
      {accounts.isError && <ErrorBanner error={accounts.error} onRetry={() => void accounts.refetch()} />}
      {report.isPending && !report.isError && period.start <= period.end && <Loading />}

      {report.data && (
        <>
          <ReportTable
            sections={sections}
            emptyMessage={
              accounts.data?.length === 0
                ? t("This book has no accounts yet. An owner creates them under Accounts and posts from an account's register -- or, coming from KMyMoney, imports the .kmy file from the books page, which makes a new book with everything in it.")
                : undefined
            }
            linkFor={(row) => `../accounts/${row.account_id}?start=${period.start}&end=${period.end}`}
            valueHeader={t("Amount")}
            totals={[
              { label: t("Income"), amount: report.data.income_earned },
              { label: t("Expenses"), amount: report.data.expenses },
              { label: t("Net"), amount: report.data.net, emphasis: true },
            ]}
          />
          {report.data.savings_rate && (
            <p className="kpi">
              Savings rate{" "}
              <strong title={`${report.data.savings_rate.num}/${report.data.savings_rate.den}`}>
                {formatRatePercent(report.data.savings_rate)}
              </strong>
              <span className="muted"> {t("of income kept")}</span>
            </p>
          )}
          <ValuationPanel conversions={report.data.conversions} unpriced={report.data.unpriced} target={report.data.target} />
        </>
      )}
    </article>
  );
}

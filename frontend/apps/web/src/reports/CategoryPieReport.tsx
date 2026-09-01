import { useLocale, useT } from "@pymoney/core/hooks";
import { PERIOD_PRESETS, categorySlices, periodFor, presetOf, slicesToCsv } from "@pymoney/core";
import type { IncomeStatementQuery } from "@pymoney/core";
import { useAccounts, useCommodities, useIncomeStatement } from "@pymoney/core/hooks";
import { useMemo } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { ReportActions } from "../components/ReportActions";
import { ValuationPanel } from "../components/ValuationPanel";
import { PieChart } from "../components/charts";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = { start: "", end: "", target: "", strict: false };

/**
 * Income or expenses for a period, as a pie. Every slice is one category's
 * own figure from the income statement -- the same request, the same numbers,
 * only drawn. Closed accounts are always in: a pie has to add up to the total.
 */
export function CategoryPieReport({ kind }: { kind: "INCOME" | "EXPENSE" }) {
  const t = useT();
  const locale = useLocale();
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
      include_closed: true,
      strict: params.strict,
    }),
    [period.start, period.end, params.target, params.strict],
  );
  const report = useIncomeStatement(bookId, query, period.start <= period.end);
  const accounts = useAccounts(bookId, { include_closed: true });
  const commodities = useCommodities(bookId);
  const target = commodities.data?.find((c) => c.code === report.data?.target);

  const slices = useMemo(
    () => (report.data && accounts.data ? categorySlices(report.data.rows, accounts.data, kind, t("Other")) : []),
    [report.data, accounts.data, kind, t],
  );
  const title = kind === "INCOME" ? t("Income by category") : t("Expenses by category");

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{title}</h1>
          <p className="muted">
            {period.start} to {period.end}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions csv={report.data ? () => slicesToCsv(slices, report.data.target) : null} filename={`${kind.toLowerCase()}-by-category-${period.start}-${period.end}.csv`} />
      </header>

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <div className="presets" role="group" aria-label={t("Period")}>
          {PERIOD_PRESETS.map((p) => (
            <button key={p.id} type="button" className={`chip${activePreset === p.id ? " chip--active" : ""}`} aria-pressed={activePreset === p.id} onClick={() => update(periodFor(p.id))}>
              {t(p.label)}
            </button>
          ))}
        </div>
        <DateField label={t("From")} value={period.start} max={period.end} onChange={(start) => update({ start })} />
        <DateField label={t("To")} value={period.end} min={period.start} onChange={(end) => update({ end })} />
        <CurrencyField currencies={commodities.data?.filter((c) => c.kind === "CURRENCY")} value={params.target} baseCurrencyId={baseCurrencyId} onChange={(target) => update({ target })} />
        <div className="toolbar__toggles">
          <Toggle label={t("Fail if anything cannot be valued")} checked={params.strict} onChange={(strict) => update({ strict })} />
        </div>
      </form>

      {period.start > period.end && (
        <div className="banner banner--warning" role="alert">
          {t("The period ends before it starts.")}
        </div>
      )}
      {report.isError && <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />}
      {report.isPending && !report.isError && period.start <= period.end && <Loading />}

      {report.data && target && (
        <>
          <PieChart slices={slices} scale={target.minor_unit_factor} commodity={report.data.target} locale={locale} linkFor={(s) => (s.accountId ? `../accounts/${s.accountId}?start=${period.start}&end=${period.end}` : null)} />
          <ValuationPanel conversions={report.data.conversions} unpriced={report.data.unpriced} target={report.data.target} />
        </>
      )}
    </article>
  );
}

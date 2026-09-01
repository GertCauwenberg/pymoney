import { useLocale, useT } from "@pymoney/core/hooks";
import { pieSlices, slicesToCsv, today } from "@pymoney/core";
import type { HoldingsQuery } from "@pymoney/core";
import { useCommodities, useHoldings } from "@pymoney/core/hooks";
import { useMemo } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { ReportActions } from "../components/ReportActions";
import { PieChart } from "../components/charts";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = { as_of: "", target: "", strict: false };

/** The holdings report's market values, as a pie: what the portfolio is made of. */
export function HoldingsPieReport() {
  const t = useT();
  const locale = useLocale();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const asOf = params.as_of || today();
  const query = useMemo<HoldingsQuery>(
    () => ({ as_of: asOf, ...(params.target ? { target_id: params.target } : {}), include_empty: false, strict: params.strict }),
    [asOf, params.target, params.strict],
  );
  const report = useHoldings(bookId, query);
  const commodities = useCommodities(bookId);
  const target = commodities.data?.find((c) => c.code === report.data?.target);
  const slices = useMemo(
    () =>
      report.data
        ? pieSlices(
            report.data.positions.map((p) => ({ label: `${p.path.split(":").pop() ?? p.path} (${p.commodity})`, minor: p.market_value.minor, accountId: p.account_id })),
            t("Other"),
          )
        : [],
    [report.data, t],
  );

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Holdings by position")}</h1>
          <p className="muted">
            {t("Valued as of")} {asOf}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions csv={report.data ? () => slicesToCsv(slices, report.data.target) : null} filename={`holdings-by-position-${asOf}.csv`} />
      </header>
      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label={t("As of")} value={asOf} onChange={(as_of) => update({ as_of })} />
        <CurrencyField currencies={commodities.data?.filter((c) => c.kind === "CURRENCY")} value={params.target} baseCurrencyId={baseCurrencyId} onChange={(target) => update({ target })} />
        <div className="toolbar__toggles">
          <Toggle label={t("Fail if anything cannot be valued")} checked={params.strict} onChange={(strict) => update({ strict })} />
        </div>
      </form>
      {report.isError && <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />}
      {report.isPending && !report.isError && <Loading />}
      {report.data && target && <PieChart slices={slices} scale={target.minor_unit_factor} commodity={report.data.target} locale={locale} linkFor={(s) => (s.accountId ? `../accounts/${s.accountId}` : null)} />}
    </article>
  );
}

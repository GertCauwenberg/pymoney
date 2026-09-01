import { useLocale, useT } from "@pymoney/core/hooks";
import { formatAmount, periodFor } from "@pymoney/core";
import type { NetWorthQuery, ReportInterval } from "@pymoney/core";
import { useCommodities, useNetWorth } from "@pymoney/core/hooks";
import { useMemo } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { ReportActions } from "../components/ReportActions";
import { ValuationPanel } from "../components/ValuationPanel";
import { LineChart } from "../components/charts";
import { CurrencyField, DateField, Field, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = { start: "", end: "", interval: "month", target: "", strict: false };

export const INTERVALS: readonly { id: ReportInterval; label: string }[] = [
  { id: "month", label: "Monthly" },
  { id: "quarter", label: "Quarterly" },
  { id: "year", label: "Yearly" },
];

/** A balance sheet at the end of every period, drawn as three lines. */
export function NetWorthReport() {
  const t = useT();
  const locale = useLocale();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const fallback = periodFor("last-12-months");
  const period = { start: params.start || fallback.start, end: params.end || fallback.end };
  const interval = (["month", "quarter", "year"].includes(params.interval) ? params.interval : "month") as ReportInterval;

  const query = useMemo<NetWorthQuery>(
    () => ({ start: period.start, end: period.end, interval, ...(params.target ? { target_id: params.target } : {}), strict: params.strict }),
    [period.start, period.end, interval, params.target, params.strict],
  );
  const report = useNetWorth(bookId, query, period.start <= period.end);
  const commodities = useCommodities(bookId);
  const target = commodities.data?.find((c) => c.code === report.data?.target);
  const points = report.data?.points ?? [];
  const first = points[0];
  const last = points[points.length - 1];

  const csv = report.data
    ? () => {
        const plain = (a: Parameters<typeof formatAmount>[0]) => formatAmount(a, { locale: "en-US" }).replace(/,/g, "");
        return ["as_of,assets,liabilities,net_worth", ...points.map((p) => `${p.as_of},${plain(p.assets)},${plain(p.liabilities)},${plain(p.net_worth)}`)].join("\r\n") + "\r\n";
      }
    : null;

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Net worth over time")}</h1>
          <p className="muted">
            {period.start} to {period.end}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions csv={csv} filename={`net-worth-${period.start}-${period.end}.csv`} />
      </header>
      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label={t("From")} value={period.start} max={period.end} onChange={(start) => update({ start })} />
        <DateField label={t("To")} value={period.end} min={period.start} onChange={(end) => update({ end })} />
        <Field label={t("Interval")}>
          <select value={interval} onChange={(e) => update({ interval: e.target.value })}>
            {INTERVALS.map((i) => (
              <option key={i.id} value={i.id}>
                {t(i.label)}
              </option>
            ))}
          </select>
        </Field>
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
          {first && last && (
            <p className="kpi">
              {t("Net worth")} <Money amount={first.net_worth} /> → <Money amount={last.net_worth} strong withCommodity />
              <span className="muted">
                {" "}
                ({t("change")} <Money amount={{ ...last.net_worth, minor: last.net_worth.minor - first.net_worth.minor }} />)
              </span>
            </p>
          )}
          <LineChart
            labels={points.map((p) => p.label)}
            scale={target.minor_unit_factor}
            commodity={report.data.target}
            locale={locale}
            series={[
              { name: t("Assets"), values: points.map((p) => p.assets.minor), colour: "#16a34a" },
              { name: t("Liabilities"), values: points.map((p) => -p.liabilities.minor), colour: "#dc2626" },
              // Last, so it is drawn on top when it coincides with the assets.
              { name: t("Net worth"), values: points.map((p) => p.net_worth.minor), colour: "#2563eb" },
            ]}
          />
          <ValuationPanel conversions={report.data.conversions} unpriced={report.data.unpriced} target={report.data.target} />
        </>
      )}
    </article>
  );
}

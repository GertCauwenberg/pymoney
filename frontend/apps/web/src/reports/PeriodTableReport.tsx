import { useLocale, useT } from "@pymoney/core/hooks";
import { PERIOD_PRESETS, csvCell, formatAmount, formatMinor, periodFor, presetOf } from "@pymoney/core";
import type { Amount, PeriodTableQuery, ReportInterval } from "@pymoney/core";
import { useCommodities, usePeriodTable } from "@pymoney/core/hooks";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { ReportActions } from "../components/ReportActions";
import { ValuationPanel } from "../components/ValuationPanel";
import { CurrencyField, DateField, Field, Toggle } from "../components/fields";
import { INTERVALS } from "./NetWorthReport";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = { start: "", end: "", interval: "month", target: "", closed: false, strict: false };

/** Income and expense accounts down, periods across: the spreadsheet view. */
export function PeriodTableReport() {
  const t = useT();
  const locale = useLocale();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const fallback = periodFor("year-to-date");
  const period = { start: params.start || fallback.start, end: params.end || fallback.end };
  const activePreset = presetOf(period);
  const interval = (["month", "quarter", "year"].includes(params.interval) ? params.interval : "month") as ReportInterval;

  const query = useMemo<PeriodTableQuery>(
    () => ({ start: period.start, end: period.end, interval, ...(params.target ? { target_id: params.target } : {}), include_closed: params.closed, strict: params.strict }),
    [period.start, period.end, interval, params.target, params.closed, params.strict],
  );
  const report = usePeriodTable(bookId, query, period.start <= period.end);
  const commodities = useCommodities(bookId);
  const target = commodities.data?.find((c) => c.code === report.data?.target);
  const scale = target?.minor_unit_factor ?? 100;
  const cell = (a: Amount, blankZero = true) => formatMinor(a.minor, scale, { locale, blankZero });

  const csv = report.data
    ? () => {
        const d = report.data;
        const plain = (a: Amount) => formatAmount(a, { locale: "en-US" }).replace(/,/g, "");
        const lines = [
          ["account", ...d.columns.map((c) => csvCell(c.label)), "total"].join(","),
          ...d.rows.map((r) => [csvCell(r.path), ...r.display_cells.map(plain), plain(r.display_total)].join(",")),
          ["Income", ...d.income_earned.map(plain)].join(","),
          ["Expenses", ...d.expenses.map(plain)].join(","),
          ["Net", ...d.net.map(plain)].join(","),
        ];
        return lines.join("\r\n") + "\r\n";
      }
    : null;

  return (
    <article className="report-page report-page--wide">
      <header className="report-page__header">
        <div>
          <h1>{t("Income and expenses by period")}</h1>
          <p className="muted">
            {period.start} to {period.end}
            {report.data && ` · in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions csv={csv} filename={`by-period-${period.start}-${period.end}.csv`} />
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
          <Toggle label={t("Include closed accounts")} checked={params.closed} onChange={(closed) => update({ closed })} />
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
      {report.data && (
        <>
          <div className="period-table__scroll">
            <table className="report period-table">
              <thead>
                <tr>
                  <th scope="col">{t("Account")}</th>
                  {report.data.columns.map((c) => (
                    <th key={c.label} scope="col" className="num" title={`${c.start} – ${c.end}`}>
                      {c.label}
                    </th>
                  ))}
                  <th scope="col" className="num">
                    {t("Total")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.data.rows.map((row) => (
                  <tr key={row.account_id} className={`report__row report__row--depth-${Math.min(row.depth, 3)}`}>
                    <th scope="row" style={{ paddingLeft: `${0.5 + row.depth}rem` }}>
                      <Link to={`../accounts/${row.account_id}?start=${period.start}&end=${period.end}`}>{row.path.split(":").pop()}</Link>
                    </th>
                    {row.display_cells.map((c, i) => (
                      <td key={i} className="num">
                        {cell(c)}
                      </td>
                    ))}
                    <td className="num">
                      <strong>{cell(row.display_total, false)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                {(
                  [
                    [t("Income"), report.data.income_earned],
                    [t("Expenses"), report.data.expenses],
                    [t("Net"), report.data.net],
                  ] as const
                ).map(([label, amounts], i) => (
                  <tr key={label} className={`report__total${i === 2 ? " report__total--emphasis" : ""}`}>
                    <th scope="row">{label}</th>
                    {amounts.map((a, j) => (
                      <td key={j} className="num">
                        {cell(a, false)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tfoot>
            </table>
          </div>
          <ValuationPanel conversions={report.data.conversions} unpriced={report.data.unpriced} target={report.data.target} />
        </>
      )}
    </article>
  );
}

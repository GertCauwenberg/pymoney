import { useLocale, useT } from "@pymoney/core/hooks";
import { budgetRemaining, formatMinor, subtractAmounts, sumAmounts } from "@pymoney/core";
import type { Amount, BudgetReportQuery, Period } from "@pymoney/core";
import { useBudgetReport, useCommodities, useSetBudgetMonths } from "@pymoney/core/hooks";
import { Fragment, useMemo, useState } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = { start: "", end: "", target: "", closed: false };

function periodFor(now: Date = new Date()): Period {
  const y = now.getFullYear();
  return { start: `${y}-01-01`, end: `${y}-12-31` };
}

/**
 * The period table, with a plan and a remaining on every budgeted row: what I
 * spent beside what I allow. A plan holds a figure per month and nothing more,
 * so each month cell edits its own. The "Monthly" column is a screen
 * convenience: it fills the months on screen in one go, and shows a figure
 * only when they already agree.
 */
export function BudgetReport() {
  const t = useT();
  const locale = useLocale();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  // A save the server refused: shown once, cleared by the next attempt.
  const [editError, setEditError] = useState<unknown>(null);
  const fallback = periodFor();
  const period = { start: params.start || fallback.start, end: params.end || fallback.end };

  const query = useMemo<BudgetReportQuery>(
    () => ({
      start: period.start,
      end: period.end,
      ...(params.target ? { target_id: params.target } : {}),
      include_closed: params.closed,
    }),
    [period.start, period.end, params.target, params.closed],
  );
  const report = useBudgetReport(bookId, query, period.start <= period.end);
  const commodities = useCommodities(bookId);
  const data = report.data;
  const target = commodities.data?.find((c) => c.code === data?.period.target);
  const scale = target?.minor_unit_factor ?? 100;
  const money = (a: Amount | null, blankZero = false) => (a ? formatMinor(a.minor, scale, { locale, blankZero }) : "—");

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Budget")}</h1>
          <p className="muted">
            {t("{start} to {end}", { start: period.start, end: period.end })}
            {data && ` · in ${data.period.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
      </header>

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label={t("From")} value={params.start} max={params.end || undefined} onChange={(start) => update({ start })} allowEmpty />
        <DateField label={t("To")} value={params.end} min={params.start || undefined} onChange={(end) => update({ end })} allowEmpty />
        <CurrencyField currencies={commodities.data} value={params.target} baseCurrencyId={baseCurrencyId} onChange={(target) => update({ target })} />
        <Toggle label={t("Include closed accounts")} checked={params.closed} onChange={(closed) => update({ closed })} />
      </form>

      {report.error && <ErrorBanner error={report.error} title={t("Could not load the budget")} />}
      {editError !== null && <ErrorBanner error={editError} title={t("Could not save the budget")} />}
      {report.isLoading && <Loading label={t("Loading the budget…")} />}

      {data && (
        <div className="report-scroll">
          <table className="report report--budget">
            <thead>
              <tr>
                <th scope="col">{t("Account")}</th>
                {data.period.columns.map((c) => (
                  <th key={c.label} scope="col" className="num">
                    {c.label}
                  </th>
                ))}
                <th scope="col" className="num">
                  {t("Monthly")}
                </th>
                <th scope="col" className="num">
                  {t("Total")}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.period.rows.map((prow, i) => {
                const brow = data.rows[i]!;
                // A placeholder only groups its children and a closed account
                // takes nothing new, so the server refuses a plan on either:
                // show the rolled-up figures, and no editor to type into.
                const editable = !brow.placeholder && !brow.closed;
                const accountName = prow.path.split(":").at(-1) ?? prow.path;
                const moved = prow.display_cells;
                const months = data.period.columns.map((c) => c.label);
                const monthly = monthlyFigure(brow.budget, scale);
                return (
                  <tr key={prow.account_id} style={{ "--depth": prow.depth } as React.CSSProperties}>
                    <th scope="row" className="report__account">
                      <span style={{ paddingLeft: `${prow.depth}rem` }}>{accountName}</span>
                    </th>
                    {data.period.columns.map((c, j) => (
                      <td key={c.label} className="num budget-cell">
                        <span className="budget-cell__spent">{money(moved[j]!)}</span>
                        {editable && (
                          <BudgetInput
                            key={`${prow.account_id}:${c.label}`}
                            bookId={bookId}
                            accountId={prow.account_id}
                            accountName={accountName}
                            month={c.label}
                            defaultValue={brow.budget[j] ? formatMinor(brow.budget[j]!.minor, scale, { locale: "en-US", blankZero: false }).replace(/,/g, "") : ""}
                            onError={setEditError}
                          />
                        )}
                        <Remaining amount={budgetRemaining(brow.budget[j] ?? null, moved[j]!)} money={money} />
                      </td>
                    ))}
                    <td className="num budget-cell">
                      {editable && (
                        <BudgetMonthly
                          key={`${prow.account_id}:monthly:${monthly || "none"}`}
                          bookId={bookId}
                          accountId={prow.account_id}
                          accountName={accountName}
                          months={months}
                          defaultValue={monthly}
                          onError={setEditError}
                        />
                      )}
                    </td>
                    <td className="num">
                      {money(prow.display_total!)}
                      {sumAmounts(brow.budget) && (
                        <Remaining amount={budgetRemaining(sumAmounts(brow.budget), prow.display_total!)} money={money} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              {/* Two blocks -- what is planned, then what is left -- each with
                  income and expenses, plus the difference under "Budgeted".
                  The server carries plans and movement only, so what is left
                  is subtracted here and the whole-period figure summed. */}
              {(
                [
                  [
                    t("Budgeted"),
                    [
                      [t("Income"), data.income.budget],
                      [t("Expenses"), data.expenses.budget],
                      [
                        t("Surplus"),
                        data.income.budget.map((b, j) => subtractAmounts(b, data.expenses.budget[j] ?? null)),
                      ],
                    ],
                  ],
                  [
                    t("Remaining"),
                    [
                      [t("Income"), data.income.budget.map((b, j) => budgetRemaining(b, data.income.spent[j]!))],
                      [t("Expenses"), data.expenses.budget.map((b, j) => budgetRemaining(b, data.expenses.spent[j]!))],
                    ],
                  ],
                ] as const
              ).map(([heading, rows]) => (
                <Fragment key={heading}>
                  <tr className="report__section">
                    <th colSpan={data.period.columns.length + 3} scope="rowgroup">
                      {heading}
                    </th>
                  </tr>
                  {rows.map(([label, values]) => (
                    <tr key={label} className="report__total">
                      <th scope="row">{label}</th>
                      {values.map((a, j) => (
                        <td key={j} className="num">
                          {money(a)}
                        </td>
                      ))}
                      <td className="num" />
                      <td className="num">{money(sumAmounts(values))}</td>
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tfoot>
          </table>
        </div>
      )}
    </article>
  );
}

function monthlyFigure(budget: readonly (Amount | null)[], scale: number): string {
  // Shown only when every month on screen already agrees: the input fills them
  // all, so a figure here means "each of these", and it cannot mean that while
  // they differ. All blank is blank, which is what "nothing planned" says.
  const minors = new Set(budget.map((b) => (b === null ? null : b.minor)));
  if (minors.size !== 1) return "";
  const minor = [...minors][0] ?? null;
  return minor === null ? "" : formatMinor(minor, scale, { locale: "en-US", blankZero: false }).replace(/,/g, "");
}

function Remaining({ amount, money }: { amount: Amount | null; money: (a: Amount | null) => string }) {
  if (!amount) return null;
  return <span className={`budget-cell__remaining${amount.minor < 0 ? " money--negative" : ""}`}>{money(amount)}</span>;
}

/** An editable figure for one month. Empty removes that month. */
function BudgetInput({ bookId, accountId, accountName, month, defaultValue, onError }: { bookId: string; accountId: string; accountName: string; month: string; defaultValue: string; onError: (error: unknown) => void }) {
  const t = useT();
  const set = useSetBudgetMonths(bookId, accountId);
  const [value, setValue] = useState(defaultValue);
  // The figure the server holds moved under us (another tab, or the monthly
  // fill changing what this month holds): show it again.
  const [shownDefault, setShownDefault] = useState(defaultValue);
  if (shownDefault !== defaultValue) {
    setShownDefault(defaultValue);
    setValue(defaultValue);
  }
  const commit = () => {
    const trimmed = value.trim();
    if (trimmed === defaultValue.trim()) return;
    onError(null);
    set.mutate(
      { months: { [month]: trimmed === "" ? null : trimmed } },
      {
        // Keep what is on screen honest: the typed figure never made it in.
        onError: (error) => {
          setValue(defaultValue);
          onError(error);
        },
      },
    );
  };
  return (
    <input
      type="text"
      inputMode="decimal"
      className="input--amount budget-cell__input"
      aria-label={t("Budget for {account} in {month}", { account: accountName, month })}
      placeholder={t("budget")}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      disabled={set.isPending}
    />
  );
}

/**
 * The "same every month" figure for the period on screen.
 *
 * It is a screen convenience, not a stored one: typing here writes that figure
 * into every month the table shows, and emptying it removes those months. A
 * plan holds months, so nothing here can reach beyond what is on screen.
 */
function BudgetMonthly({ bookId, accountId, accountName, months, defaultValue, onError }: { bookId: string; accountId: string; accountName: string; months: readonly string[]; defaultValue: string; onError: (error: unknown) => void }) {
  const t = useT();
  const set = useSetBudgetMonths(bookId, accountId);
  const [value, setValue] = useState(defaultValue);
  const commit = () => {
    const trimmed = value.trim();
    if (trimmed === defaultValue.trim()) return;
    onError(null);
    const fill = trimmed === "" ? null : trimmed;
    set.mutate(
      { months: Object.fromEntries(months.map((m) => [m, fill])) },
      {
        onError: (error) => {
          setValue(defaultValue);
          onError(error);
        },
      },
    );
  };
  return (
    <input
      type="text"
      inputMode="decimal"
      className="input--amount budget-cell__input"
      aria-label={t("Monthly budget for {account}", { account: accountName })}
      placeholder={t("none")}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
      disabled={set.isPending}
    />
  );
}

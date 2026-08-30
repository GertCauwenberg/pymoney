import { useT } from "@pymoney/core/hooks";
import { formatAmount, formatRate, today } from "@pymoney/core";
import type { Holding, HoldingsQuery } from "@pymoney/core";
import { useCommodities, useHoldings } from "@pymoney/core/hooks";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { ReportActions } from "../components/ReportActions";
import { CurrencyField, DateField, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "./useReportParams";

const DEFAULTS = {
  as_of: "",
  target: "",
  empty: false,
  strict: false,
};

function holdingsCsv(positions: readonly Holding[]): string {
  const plain = (a: Parameters<typeof formatAmount>[0]) => formatAmount(a, { locale: "en-US" }).replace(/,/g, "");
  const lines = positions.map((p) =>
    [
      /[",\n]/.test(p.path) ? `"${p.path.replace(/"/g, '""')}"` : p.path,
      p.commodity,
      plain(p.quantity),
      p.unit_price ? p.unit_price.approx : "",
      plain(p.cost),
      p.cost.commodity,
      plain(p.market_value),
      p.market_value.commodity,
      p.unrealised_gain ? plain(p.unrealised_gain) : "",
    ].join(","),
  );
  return ["account,commodity,quantity,unit_price,cost,cost_commodity,market_value,value_commodity,unrealised_gain", ...lines].join("\r\n") + "\r\n";
}

export function HoldingsReport() {
  const t = useT();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const asOf = params.as_of || today();

  const query = useMemo<HoldingsQuery>(
    () => ({
      as_of: asOf,
      ...(params.target ? { target_id: params.target } : {}),
      include_empty: params.empty,
      strict: params.strict,
    }),
    [asOf, params.target, params.empty, params.strict],
  );

  const report = useHoldings(bookId, query);
  const currencies = useCommodities(bookId, { kind: "CURRENCY" });

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Holdings")}</h1>
          <p className="muted">
            As of {asOf}
            {report.data && ` · valued in ${report.data.target}`}
            {report.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <ReportActions
          csv={report.data ? () => holdingsCsv(report.data.positions) : null}
          filename={`holdings-${asOf}.csv`}
        />
      </header>

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label={t("As of")} value={asOf} onChange={(as_of) => update({ as_of })} />
        <CurrencyField
          label={t("Value in")}
          currencies={currencies.data}
          value={params.target}
          baseCurrencyId={baseCurrencyId}
          onChange={(target) => update({ target })}
        />
        <div className="toolbar__toggles">
          <Toggle label={t("Include empty positions")} checked={params.empty} onChange={(empty) => update({ empty })} />
          <Toggle
            label={t("Fail if anything cannot be valued")}
            checked={params.strict}
            onChange={(strict) => update({ strict })}
          />
        </div>
      </form>

      {report.isError && <ErrorBanner error={report.error} onRetry={() => void report.refetch()} />}
      {report.isPending && !report.isError && <Loading />}

      {report.data &&
        (report.data.positions.length === 0 ? (
          <p className="empty">No security positions{params.empty ? "" : " with a non-zero quantity"}.</p>
        ) : (
          <table className="report">
            <thead>
              <tr>
                <th scope="col">{t("Account")}</th>
                <th scope="col">{t("Security")}</th>
                <th scope="col" className="num">
                  {t("Quantity")}
                </th>
                <th scope="col" className="num">
                  {t("Price")}
                </th>
                <th scope="col" className="num">
                  {t("Cost")}
                </th>
                <th scope="col" className="num">
                  {t("Market value")}
                </th>
                <th scope="col" className="num">
                  {t("Unrealised")}
                </th>
              </tr>
            </thead>
            <tbody>
              {report.data.positions.map((p) => (
                <tr key={p.account_id}>
                  <td>
                    <Link to={`../accounts/${p.account_id}?end=${asOf}`} className="account-link" title={t("Open the register")}>
                      {p.path}
                    </Link>
                  </td>
                  <td>{p.commodity}</td>
                  <td className="num">
                    <Money amount={p.quantity} />
                  </td>
                  <td className="num">
                    {p.unit_price ? (
                      <span title={`${p.unit_price.num}/${p.unit_price.den}`}>{formatRate(p.unit_price)}</span>
                    ) : (
                      <span className="muted">{t("unpriced")}</span>
                    )}
                  </td>
                  <td className="num">
                    <Money amount={p.cost} withCommodity={p.cost.commodity_id !== p.market_value.commodity_id} />
                  </td>
                  <td className="num">
                    <Money amount={p.market_value} />
                  </td>
                  <td className="num">
                    <Money amount={p.unrealised_gain} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="report__total report__total--emphasis">
                <th scope="row" colSpan={4}>
                  {t("Total")}
                </th>
                <td className="num">
                  <Money amount={report.data.total_cost} strong withCommodity />
                </td>
                <td className="num">
                  <Money amount={report.data.total_value} strong withCommodity />
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        ))}
    </article>
  );
}

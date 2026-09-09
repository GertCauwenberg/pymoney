import { useT } from "@pymoney/core/hooks";
import { parseDecimal } from "@pymoney/core";
import type { Account, Transaction, TransactionSearchQuery } from "@pymoney/core";
import { useAccounts, useCommodities, usePayees, useTags, useTransactionSearch } from "@pymoney/core/hooks";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { DateField, Field, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "../reports/useReportParams";
import { ReconcileMark } from "./ReconcileMark";

const DEFAULTS = {
  q: "",
  amount: "",
  min: "",
  max: "",
  start: "",
  end: "",
  account: "",
  children: false,
  payee: "",
  tag: "",
};

/** `Checking → Groceries`: where the money came from and where it went. */
export function describeAccounts(t: Transaction, accounts: ReadonlyMap<string, Account>): string {
  const name = (id: string) => accounts.get(id)?.name ?? "?";
  const unique = (ids: string[]) => [...new Set(ids.map(name))].join(", ");
  const froms = t.splits.filter((s) => s.value.minor < 0).map((s) => s.account_id);
  const tos = t.splits.filter((s) => s.value.minor > 0).map((s) => s.account_id);
  return froms.length && tos.length ? `${unique(froms)} → ${unique(tos)}` : unique(t.splits.map((s) => s.account_id));
}

/**
 * Finding a transaction. Every criterion narrows (they combine with AND); the
 * criteria live in the URL so a search can be refined, bookmarked or shared.
 * Amounts are typed as decimals and sent exactly as typed -- the server does
 * the comparing, in exact arithmetic, against a leg's quantity *or* its value.
 */
export function SearchPage() {
  const t = useT();
  const { bookId, baseCurrencyId } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const accounts = useAccounts(bookId, { include_closed: true });
  const payees = usePayees(bookId);
  const tags = useTags(bookId);
  const commodities = useCommodities(bookId);
  const currency = commodities.data?.find((c) => c.id === baseCurrencyId);
  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  const payeesById = useMemo(() => new Map((payees.data ?? []).map((p) => [p.id, p.name])), [payees.data]);

  const clean = (s: string) => s.trim().replace(",", ".");
  const amountProblem = [params.amount, params.min, params.max].some((v) => v.trim() !== "" && parseDecimal(clean(v), 1_000_000) === null)
    ? t("Amounts are plain decimals, like 84.20.")
    : params.min.trim() && params.max.trim() && Number(clean(params.min)) > Number(clean(params.max))
      ? t("The smallest amount is larger than the largest.")
      : null;
  const dateProblem = params.start && params.end && params.start > params.end ? t("The period ends before it starts.") : null;
  const empty =
    !params.q.trim() &&
    !params.amount.trim() &&
    !params.min.trim() &&
    !params.max.trim() &&
    !params.start &&
    !params.end &&
    !params.account &&
    !params.payee &&
    !params.tag;

  const query = useMemo<TransactionSearchQuery | null>(() => {
    if (empty || amountProblem || dateProblem) return null;
    return {
      ...(params.q.trim() ? { q: params.q.trim() } : {}),
      ...(params.amount.trim() ? { amount: clean(params.amount) } : {}),
      ...(params.min.trim() ? { amount_min: clean(params.min) } : {}),
      ...(params.max.trim() ? { amount_max: clean(params.max) } : {}),
      ...(params.start ? { start: params.start } : {}),
      ...(params.end ? { end: params.end } : {}),
      ...(params.account ? { account_id: params.account, include_children: params.children } : {}),
      ...(params.payee ? { payee_id: params.payee } : {}),
      ...(params.tag ? { tag_id: params.tag } : {}),
      limit: 200,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, empty, amountProblem, dateProblem]);
  const results = useTransactionSearch(bookId, query);

  const sortedAccounts = useMemo(() => [...(accounts.data ?? [])].sort((a, b) => a.path.localeCompare(b.path)), [accounts.data]);
  const sortedPayees = useMemo(() => [...(payees.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [payees.data]);
  const sortedTags = useMemo(() => [...(tags.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)), [tags.data]);

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <h1>{t("Find transactions")}</h1>
          <p className="muted">{t("Every criterion narrows the search; fill in one or more.")}</p>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={() => update({ ...DEFAULTS })} disabled={empty}>
            {t("Clear")}
          </button>
        </div>
      </header>

      <form className="toolbar search-form" onSubmit={(e) => e.preventDefault()}>
        <Field label={t("Text")} hint={t("A word from the memo, or a cheque number")}>
          <input type="search" value={params.q} onChange={(e) => update({ q: e.target.value })} className="search-form__text" />
        </Field>
        <Field label={t("Amount")} hint={t("exactly")}>
          <input type="text" inputMode="decimal" value={params.amount} onChange={(e) => update({ amount: e.target.value })} className="input--amount" />
        </Field>
        <Field label={t("or between")}>
          <input type="text" inputMode="decimal" value={params.min} onChange={(e) => update({ min: e.target.value })} className="input--amount" aria-label={t("Smallest amount")} />
        </Field>
        <Field label={t("and")}>
          <input type="text" inputMode="decimal" value={params.max} onChange={(e) => update({ max: e.target.value })} className="input--amount" aria-label={t("Largest amount")} />
        </Field>
        <DateField label={t("From")} value={params.start} max={params.end || undefined} onChange={(start) => update({ start })} allowEmpty />
        <DateField label={t("To")} value={params.end} min={params.start || undefined} onChange={(end) => update({ end })} allowEmpty />
        <Field label={t("Account")}>
          <select value={params.account} onChange={(e) => update({ account: e.target.value })}>
            <option value="">{t("Any account")}</option>
            {sortedAccounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.path}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Payee")}>
          <select value={params.payee} onChange={(e) => update({ payee: e.target.value })}>
            <option value="">{t("Any payee")}</option>
            {sortedPayees.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Tag")}>
          <select value={params.tag} onChange={(e) => update({ tag: e.target.value })}>
            <option value="">{t("Any tag")}</option>
            {sortedTags.map((tg) => (
              <option key={tg.id} value={tg.id}>
                {tg.name}
              </option>
            ))}
          </select>
        </Field>
        <div className="toolbar__toggles">
          <Toggle label={t("Include sub-accounts")} checked={params.children} onChange={(children) => update({ children })} />
        </div>
      </form>

      {amountProblem && (
        <div className="banner banner--warning" role="alert">
          {amountProblem}
        </div>
      )}
      {dateProblem && (
        <div className="banner banner--warning" role="alert">
          {dateProblem}
        </div>
      )}
      {results.isError && <ErrorBanner error={results.error} onRetry={() => void results.refetch()} />}
      {query && results.isPending && !results.isError && <Loading />}

      {results.data && query && (
        <>
          <p className="muted search-summary" role="status">
            {results.data.has_more
              ? t("More than {n} transactions match; showing the newest. Narrow the search.", { n: results.data.items.length })
              : results.data.items.length === 0
                ? t("Nothing matches.")
                : results.data.items.length === 1
                  ? t("One transaction matches.")
                  : t("{n} transactions match.", { n: results.data.items.length })}
            {results.isPlaceholderData && " · updating…"}
          </p>
          {results.data.items.length > 0 && (
            <table className="report register search-results">
              <thead>
                <tr>
                  <th scope="col">{t("Date")}</th>
                  <th scope="col">{t("Payee / Memo")}</th>
                  <th scope="col">{t("Accounts")}</th>
                  <th scope="col" className="register__mark" title={t("Reconciliation")}>
                    R
                  </th>
                  <th scope="col" className="num">
                    {t("Amount")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {results.data.items.map((tx) => {
                  const debits = tx.splits.reduce((acc, s) => acc + Math.max(0, s.value.minor), 0);
                  const total = tx.splits[0] ? { ...tx.splits[0].value, minor: debits } : null;
                  const payee = tx.payee_id ? payeesById.get(tx.payee_id) : undefined;
                  return (
                    <tr key={tx.id} className="register__row">
                      <td className="register__date">
                        <Link to={`../transactions/${tx.id}`} className="row-link">
                          {tx.date}
                        </Link>
                      </td>
                      <td>
                        <Link to={`../transactions/${tx.id}`} className="row-link">
                          {payee ? (
                            <>
                              <span className="register__payee">{payee}</span>
                              {tx.memo && <span className="register__memo muted"> — {tx.memo}</span>}
                            </>
                          ) : (
                            <span className="register__payee">{tx.memo || <span className="muted">(no memo)</span>}</span>
                          )}
                        </Link>
                      </td>
                      <td className="muted">{describeAccounts(tx, accountsById)}</td>
                      <td className="register__mark">
                        <ReconcileMark state={tx.reconcile_state} />
                      </td>
                      <td className="num">{total && <Money amount={total} withCommodity={!currency || total.commodity_id !== currency.id} />}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </article>
  );
}

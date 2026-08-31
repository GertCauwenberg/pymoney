import { useT } from "@pymoney/core/hooks";
import { formatAmount, naturalSign } from "@pymoney/core";
import type { Account, Amount, ReconcileState, RegisterRow } from "@pymoney/core";
import { useAccount, useAccounts, useCommodities, useLedger, usePayees, useReconcileAccount } from "@pymoney/core/hooks";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { ReportActions } from "../components/ReportActions";
import { DateField, Field, Toggle } from "../components/fields";
import { useBookContext, useReportParams } from "../reports/useReportParams";
import { ReconcileMark } from "./ReconcileMark";
import { TransactionForm } from "./TransactionForm";

const CAN_WRITE = new Set(["editor", "owner"]);

const DEFAULTS = { start: "", end: "", children: false };

/**
 * Every leg booked to one account, oldest first, with a running balance.
 *
 * Arrives here from a report row, so the window defaults to the report's.
 * Clear both dates to see the whole history. Amounts are shown with the
 * sign the reports use (income positive), so the closing balance here is
 * the figure that was clicked on there.
 */
export function AccountRegisterPage() {
  const t = useT();
  const { bookId, baseCurrencyId, role } = useBookContext();
  const { accountId = "" } = useParams();
  const [params, update] = useReportParams(DEFAULTS);
  const [adding, setAdding] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [statementDate, setStatementDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [targetState, setTargetState] = useState<ReconcileState>("RECONCILED");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const account = useAccount(bookId, accountId);
  const accounts = useAccounts(bookId, { include_closed: true });
  const payees = usePayees(bookId);
  const commodities = useCommodities(bookId);
  const currency = commodities.data?.find((c) => c.id === baseCurrencyId);
  const canWrite = role !== null && CAN_WRITE.has(role);
  const canPost = canWrite && account.data !== undefined && !account.data.closed && !account.data.placeholder;
  const query = useMemo(
    () => ({
      ...(params.start ? { start: params.start } : {}),
      ...(params.end ? { end: params.end } : {}),
      include_children: params.children,
    }),
    [params.start, params.end, params.children],
  );
  const validWindow = !params.start || !params.end || params.start <= params.end;
  const ledger = useLedger(bookId, accountId, query, validWindow);
  const reconcile = useReconcileAccount(bookId, accountId);

  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  const payeesById = useMemo(() => new Map((payees.data ?? []).map((p) => [p.id, p.name])), [payees.data]);

  const rows = useMemo(() => ledger.data?.pages.flatMap((p) => p.items) ?? [], [ledger.data]);
  const totals = ledger.data?.pages[0]?.totals;
  const translated = totals?.translated ?? false;
  const conversions = totals?.conversions ?? [];
  const unpriced = totals?.unpriced ?? [];
  const cls = account.data?.account_class ?? "ASSET";
  const sign = (a: Amount | null | undefined) => (a ? naturalSign(a, cls) : a);
  // A Value column earns its place only when some leg was booked in another
  // commodity than the account's (a USD wallet paid from a EUR account).
  const showValue = rows.some((r) => r.value.commodity_id !== r.quantity.commodity_id);
  const showChildren = params.children || rows.some((r) => r.account_id !== accountId);
  // Only this account's own splits can go on this account's statement, and a
  // frozen one is already beyond changing.
  const markable = (r: RegisterRow) => r.account_id === accountId && r.reconcile_state !== "FROZEN";
  const startReconciling = () => {
    // What the bank has seen is the natural starting selection.
    setSelected(new Set(rows.filter((r) => markable(r) && r.reconcile_state === "CLEARED").map((r) => r.split_id)));
    setTargetState("RECONCILED");
    reconcile.reset();
    setReconciling(true);
  };
  const toggle = (splitId: string, on: boolean) =>
    setSelected((old) => {
      const next = new Set(old);
      if (on) next.add(splitId);
      else next.delete(splitId);
      return next;
    });
  const selectedRows = rows.filter((r) => selected.has(r.split_id));
  const selectedSum = selectedRows.reduce((acc, r) => acc + BigInt(r.quantity.minor), 0n);
  const applyReconcile = () =>
    reconcile.mutate(
      { split_ids: [...selected], state: targetState, statement_date: statementDate },
      { onSuccess: () => setReconciling(false) },
    );

  if (account.isError) {
    return (
      <article className="report-page">
        <ErrorBanner error={account.error} onRetry={() => void account.refetch()} />
      </article>
    );
  }

  const csv = rows.length
    ? () => registerCsv(rows, cls, accountsById, payeesById)
    : null;

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <Breadcrumbs account={account.data} accountsById={accountsById} />
          <h1>{account.data?.name ?? "…"}</h1>
          <p className="muted">
            {account.data && (
              <>
                {account.data.type.toLowerCase().replace("_", " ")} · {account.data.commodity}
                {account.data.closed && " · closed"}
                {account.data.placeholder && " · placeholder"}
                {account.data.institution && ` · ${account.data.institution}`}
              </>
            )}
            {ledger.isPlaceholderData && " · updating…"}
          </p>
        </div>
        <div className="actions">
          {canPost && !adding && (
            <button type="button" className="btn btn--primary" onClick={() => setAdding(true)}>
              {t("New transaction")}
            </button>
          )}
          {canWrite && !reconciling && account.data && !account.data.placeholder && (
            <button type="button" className="btn" onClick={startReconciling}>
              {t("Reconcile")}
            </button>
          )}
          {role === "owner" && (
            <Link to={`../accounts?edit=${accountId}`} className="btn">
              {t("Edit account")}
            </Link>
          )}
          <ReportActions csv={csv} filename={`register-${account.data?.name ?? accountId}.csv`} />
        </div>
      </header>

      {adding && accounts.data && payees.data && commodities.data && currency && (
        <TransactionForm
          bookId={bookId}
          accounts={accounts.data}
          payees={payees.data}
          commodities={commodities.data}
          currency={currency}
          defaultAccountId={accountId}
          onSaved={() => setAdding(false)}
          onCancel={() => setAdding(false)}
        />
      )}

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <DateField label={t("From")} value={params.start} max={params.end || undefined} onChange={(start) => update({ start })} allowEmpty />
        <DateField label={t("To")} value={params.end} min={params.start || undefined} onChange={(end) => update({ end })} allowEmpty />
        <div className="toolbar__toggles">
          <Toggle label={t("Include sub-accounts")} checked={params.children} onChange={(children) => update({ children })} />
          {(params.start || params.end) && (
            <button type="button" className="btn btn--link" onClick={() => update({ start: "", end: "" })}>
              {t("Show all dates")}
            </button>
          )}
        </div>
      </form>

      {reconciling && (
        <form
          className="card reconcile-bar"
          aria-label={t("Reconcile against a statement")}
          onSubmit={(e) => {
            e.preventDefault();
            applyReconcile();
          }}
        >
          <h2>{t("Reconcile against a statement")}</h2>
          <p className="muted">
            {t("Tick every entry that is on the statement, then mark them all at once. Cleared entries are pre-ticked.")}
          </p>
          {reconcile.error && <ErrorBanner error={reconcile.error} title={t("Could not reconcile")} />}
          <div className="toolbar">
            <DateField label={t("Statement date")} value={statementDate} onChange={setStatementDate} />
            <Field label={t("Mark as")}>
              <select value={targetState} onChange={(e) => setTargetState(e.target.value as ReconcileState)}>
                <option value="CLEARED">{t("Cleared")}</option>
                <option value="RECONCILED">{t("Reconciled")}</option>
                {/* Irreversible, so only an owner may -- and the server checks. */}
                {role === "owner" && <option value="FROZEN">{t("Frozen")}</option>}
              </select>
            </Field>
            <span className="reconcile-bar__sum">
              {t("{n} selected, totalling", { n: selected.size })}{" "}
              {selectedRows[0] ? (
                <Money amount={sign({ ...selectedRows[0].quantity, minor: Number(selectedSum) }) as Amount} strong />
              ) : (
                <span className="muted">—</span>
              )}
            </span>
            <div className="actions">
              <button type="submit" className="btn btn--primary" disabled={selected.size === 0 || reconcile.isPending}>
                {reconcile.isPending ? "…" : t("Mark them")}
              </button>
              <button type="button" className="btn" onClick={() => setReconciling(false)}>
                {t("Cancel")}
              </button>
            </div>
          </div>
          {targetState === "FROZEN" && (
            <p className="banner banner--warning">{t("Frozen is final: these entries can never be edited or deleted again.")}</p>
          )}
        </form>
      )}

      {!validWindow && (
        <div className="banner banner--warning" role="alert">
          {t("The period ends before it starts.")}
        </div>
      )}
      {ledger.isError && <ErrorBanner error={ledger.error} onRetry={() => void ledger.refetch()} />}
      {ledger.isPending && validWindow && !ledger.isError && <Loading />}

      {ledger.data && (
        <>
          <table className="report register">
            <thead>
              <tr>
                <th scope="col">{t("Date")}</th>
                <th scope="col" className="register__num">
                  {t("No.")}
                </th>
                <th scope="col">{t("Payee / Memo")}</th>
                {showChildren && <th scope="col">{t("Account")}</th>}
                <th scope="col" className="register__mark" title={t("Reconciliation")}>
                  R
                </th>
                {showValue && (
                  <th scope="col" className="num">
                    {t("Value")}
                  </th>
                )}
                <th scope="col" className="num">
                  {t("Amount")}
                </th>
                <th
                  scope="col"
                  className="num"
                  title={
                    translated
                      ? `Sub-accounts in other commodities are translated into ${account.data?.commodity ?? "the account's commodity"} at one rate: the window's end date, or today.`
                      : undefined
                  }
                >
                  {translated ? "Balance ≈" : t("Balance")}
                </th>
              </tr>
            </thead>
            <tbody>
              {totals?.opening && (
                <tr className="register__balance-row">
                  <td>{params.start || "—"}</td>
                  <td />
                  <td className="muted" colSpan={showChildren ? 2 : 1}>
                    {t("Opening balance")}
                  </td>
                  <td />
                  {showValue && <td />}
                  <td />
                  <td className="num">
                    <Money amount={sign(totals.opening)} strong />
                  </td>
                </tr>
              )}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5 + (showChildren ? 1 : 0) + (showValue ? 1 : 0) + 1} className="empty">
                    No entries{params.start || params.end ? " in this period" : ""}.
                  </td>
                </tr>
              )}
              {rows.map((row) => (
                <tr key={row.split_id} className={`register__row reconcile-row--${row.reconcile_state.toLowerCase()}`}>
                  <td className="register__date">
                    <Link to={`../transactions/${row.transaction_id}`} className="row-link">
                      {row.date}
                    </Link>
                  </td>
                  <td className="register__num muted">{row.number}</td>
                  <td>
                    <Link to={`../transactions/${row.transaction_id}`} className="row-link">
                      {row.payee_id && payeesById.get(row.payee_id) ? (
                        <>
                          <span className="register__payee">{payeesById.get(row.payee_id)}</span>
                          {row.memo && <span className="register__memo muted"> — {row.memo}</span>}
                        </>
                      ) : (
                        <span className="register__payee">{row.memo || <span className="muted">(no memo)</span>}</span>
                      )}
                    </Link>
                  </td>
                  {showChildren && (
                    <td className="muted">
                      {row.account_id === accountId ? "" : accountsById.get(row.account_id)?.name ?? "?"}
                    </td>
                  )}
                  <td className="register__mark">
                    {reconciling && markable(row) ? (
                      <input
                        type="checkbox"
                        aria-label={t("On the statement")}
                        checked={selected.has(row.split_id)}
                        onChange={(e) => toggle(row.split_id, e.target.checked)}
                      />
                    ) : (
                      <ReconcileMark state={row.reconcile_state} />
                    )}
                  </td>
                  {showValue && (
                    <td className="num">
                      <Money amount={sign(row.value)} withCommodity={row.value.commodity_id !== row.quantity.commodity_id} />
                    </td>
                  )}
                  <td className="num">
                    <Money amount={sign(row.quantity)} />
                  </td>
                  <td className="num">
                    <Money amount={sign(row.running_balance)} />
                  </td>
                </tr>
              ))}
            </tbody>
            {totals?.closing && !ledger.hasNextPage && (
              <tfoot>
                <tr className="report__total report__total--emphasis">
                  <th scope="row" colSpan={5 + (showChildren ? 1 : 0) + (showValue ? 1 : 0)}>
                    {params.end ? `Balance on ${params.end}` : t("Balance")}
                  </th>
                  <td className="num">
                    <Money amount={sign(totals.closing)} strong withCommodity />
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
          {translated && totals && (
            <aside className="register__translation muted">
              {conversions.length > 0 && (
                <>
                  Running balance and totals translated into {account.data?.commodity} at one rate
                  {params.end ? ` (as of ${params.end})` : " (today's)"}:{" "}
                  {conversions
                    .map((c) => `1 ${c.base} = ${c.rate.approx.replace(/0+$/, "").replace(/\.$/, "")} ${c.quote} (${c.sources.join(", ")})`)
                    .join("; ")}
                  .
                </>
              )}
              {unpriced.length > 0 && (
                <span className="money--negative">
                  {" "}
                  No price for {unpriced.join(", ")}: the running balance stops at the first such row and the
                  totals are not shown. Add a price under Prices.
                </span>
              )}
            </aside>
          )}
          {ledger.hasNextPage && (
            <div className="load-more">
              <button
                type="button"
                className="btn"
                onClick={() => void ledger.fetchNextPage()}
                disabled={ledger.isFetchingNextPage}
              >
                {ledger.isFetchingNextPage ? t("Loading…") : t("Load more")}
              </button>
              <span className="muted">
                {rows.length} shown · closing balance{" "}
                <Money amount={sign(totals?.closing)} />
              </span>
            </div>
          )}
        </>
      )}
    </article>
  );
}

function Breadcrumbs({
  account,
  accountsById,
}: {
  account: Account | undefined;
  accountsById: Map<string, Account>;
}) {
  const t = useT();
  if (!account) return <p className="breadcrumbs muted">&nbsp;</p>;
  const chain: Account[] = [];
  let cursor = account.parent_id ? accountsById.get(account.parent_id) : undefined;
  while (cursor) {
    chain.unshift(cursor);
    cursor = cursor.parent_id ? accountsById.get(cursor.parent_id) : undefined;
  }
  return (
    <p className="breadcrumbs muted">
      <Link to="../reports/balance-sheet">{t("Reports")}</Link>
      {chain.map((a) => (
        <span key={a.id}>
          {" › "}
          <Link to={`../accounts/${a.id}`}>{a.name}</Link>
        </span>
      ))}
    </p>
  );
}

function registerCsv(
  rows: readonly RegisterRow[],
  cls: Account["account_class"],
  accountsById: Map<string, Account>,
  payeesById: Map<string, string>,
): string {
  const cell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const plain = (a: Amount | null | undefined) =>
    a ? formatAmount(naturalSign(a, cls), { locale: "en-US" }).replace(/,/g, "") : "";
  const header = "date,number,payee,memo,account,reconciled,amount,commodity,value,value_commodity,balance";
  const lines = rows.map((r) =>
    [
      r.date,
      cell(r.number),
      cell(r.payee_id ? payeesById.get(r.payee_id) ?? "" : ""),
      cell(r.memo),
      cell(accountsById.get(r.account_id)?.path ?? ""),
      r.reconcile_state,
      plain(r.quantity),
      r.quantity.commodity,
      plain(r.value),
      r.value.commodity,
      plain(r.running_balance),
    ].join(","),
  );
  return [header, ...lines].join("\r\n") + "\r\n";
}

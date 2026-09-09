import { useT } from "@pymoney/core/hooks";
import { formatRate } from "@pymoney/core";
import type { ReconcileState, Split } from "@pymoney/core";
import { useAccounts, useCommodities, useDeleteTransaction, useHistory, usePayees, useSetSplitState, useTags, useTransaction } from "@pymoney/core/hooks";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { HistoryTable } from "../components/HistoryTable";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { useBookContext } from "../reports/useReportParams";
import { ReconcileMark } from "./ReconcileMark";
import { TransactionForm } from "./TransactionForm";

const CAN_WRITE = new Set(["editor", "owner"]);

/**
 * One transaction, every leg. Editors and owners can edit or delete it, unless
 * a split is reconciled -- then the server refuses and the buttons say why.
 *
 * Values are shown as stored: debits positive, credits negative, and they
 * sum to zero in the transaction currency -- that is what double entry means,
 * and a detail view is the one place not to hide it behind natural signs.
 */
export function TransactionPage() {
  const tr = useT();
  const { bookId, role } = useBookContext();
  const { transactionId = "" } = useParams();
  const tx = useTransaction(bookId, transactionId);
  const accounts = useAccounts(bookId, { include_closed: true });
  const payees = usePayees(bookId);
  const tags = useTags(bookId);
  const commodities = useCommodities(bookId);
  const remove = useDeleteTransaction(bookId);
  const mark = useSetSplitState(bookId);
  const history = useHistory(bookId, { entity_id: transactionId });
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  const payeesById = useMemo(() => new Map((payees.data ?? []).map((p) => [p.id, p.name])), [payees.data]);
  const tagsById = useMemo(() => new Map((tags.data ?? []).map((tg) => [tg.id, tg.name])), [tags.data]);

  if (tx.isPending) return <Loading label={tr("Loading the transaction…")} />;
  if (tx.isError) {
    return (
      <article className="report-page">
        <ErrorBanner error={tx.error} onRetry={() => void tx.refetch()} />
      </article>
    );
  }
  const t = tx.data;
  const payee = t.payee_id ? payeesById.get(t.payee_id) : undefined;
  const sum = t.splits.reduce((acc, s) => acc + s.value.minor, 0);
  const currency = t.splits[0]?.value;
  const canWrite = role !== null && CAN_WRITE.has(role);
  const lockedHint = t.locked ? "A reconciled transaction cannot be changed; unreconcile its splits first." : undefined;
  const txCurrency = commodities.data?.find((c) => c.id === t.currency_id);
  const firstAccount = t.splits[0]?.account_id;

  const onDelete = () =>
    remove.mutate(t.id, {
      onSuccess: () => void navigate(firstAccount ? `../accounts/${firstAccount}` : "../reports/balance-sheet"),
    });

  return (
    <article className="report-page transaction">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../reports/balance-sheet">{tr("Reports")}</Link> › Transaction
          </p>
          <h1>{payee ?? (t.memo || tr("Transaction"))}</h1>
          <p className="muted">
            {t.date}
            {t.number && ` · no. ${t.number}`}
            {payee && t.memo && ` · ${t.memo}`}
            {` · ${t.currency}`}
            {t.locked && " · locked"}
          </p>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={() => void navigate(-1)}>
            ← Back
          </button>
          {canWrite && !editing && (
            <>
              <button type="button" className="btn btn--primary" disabled={t.locked} title={lockedHint} onClick={() => setEditing(true)}>
                {tr("Edit")}
              </button>
              {confirming ? (
                <span className="actions actions--inline">
                  <span className="muted">{tr("Delete this transaction?")}</span>
                  <button type="button" className="btn btn--small btn--danger" disabled={remove.isPending} onClick={onDelete}>
                    {tr("Yes, delete")}
                  </button>
                  <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
                    {tr("No")}
                  </button>
                </span>
              ) : (
                <button type="button" className="btn btn--danger" disabled={t.locked} title={lockedHint} onClick={() => setConfirming(true)}>
                  {tr("Delete")}
                </button>
              )}
            </>
          )}
          <button type="button" className="btn" onClick={() => window.print()}>
            {tr("Print")}
          </button>
        </div>
      </header>

      {remove.error && <ErrorBanner error={remove.error} title={tr("Could not delete")} />}
      {mark.error && <ErrorBanner error={mark.error} title={tr("Could not change the reconciliation state")} />}

      {editing && accounts.data && payees.data && commodities.data && txCurrency && (
        <TransactionForm
          bookId={bookId}
          accounts={accounts.data}
          payees={payees.data}
          commodities={commodities.data}
          currency={txCurrency}
          existing={t}
          onSaved={() => setEditing(false)}
          onCancel={() => setEditing(false)}
        />
      )}

      <dl className="facts">
        <div>
          <dt>{tr("Date")}</dt>
          <dd>{t.date}</dd>
        </div>
        <div>
          <dt>{tr("Payee")}</dt>
          <dd>{payee ?? <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>{tr("Memo")}</dt>
          <dd>{t.memo || <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>{tr("Number")}</dt>
          <dd>{t.number || <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>{tr("Currency")}</dt>
          <dd>{t.currency}</dd>
        </div>
        <div>
          <dt>{tr("Reconciliation")}</dt>
          <dd>
            <ReconcileMark state={t.reconcile_state} /> {t.reconcile_state.toLowerCase().replace("_", " ")}
          </dd>
        </div>
        {t.entered_at && (
          <div>
            <dt>{tr("Entered")}</dt>
            <dd>{new Date(t.entered_at).toLocaleString()}</dd>
          </div>
        )}
        {t.imported_id && (
          <div>
            <dt>{tr("Imported as")}</dt>
            <dd>
              <code>{t.imported_id}</code>
            </dd>
          </div>
        )}
        <div>
          <dt>{tr("Revision")}</dt>
          <dd>{t.revision}</dd>
        </div>
      </dl>

      <h2>{tr("Splits")}</h2>
      <table className="report splits">
        <thead>
          <tr>
            <th scope="col">{tr("Account")}</th>
            <th scope="col">{tr("Memo")}</th>
            <th scope="col" className="register__mark" title={tr("Reconciliation")}>
              R
            </th>
            <th scope="col" className="num">
              {tr("Quantity")}
            </th>
            <th scope="col" className="num">
              {tr("Price")}
            </th>
            <th scope="col" className="num">
              {tr("Value")}
            </th>
          </tr>
        </thead>
        <tbody>
          {t.splits.map((s) => (
            <SplitRow
              key={s.id}
              split={s}
              accountPath={accountsById.get(s.account_id)?.path}
              tagNames={(s.tag_ids ?? []).map((id) => tagsById.get(id) ?? "?")}
              onMark={
                canWrite
                  ? (state) =>
                      mark.mutate({
                        transactionId: t.id,
                        splitId: s.id,
                        body: {
                          state,
                          on: state === "NOT_RECONCILED" ? null : new Date().toISOString().slice(0, 10),
                        },
                      })
                  : undefined
              }
              canFreeze={role === "owner"}
              busy={mark.isPending}
            />
          ))}
        </tbody>
        <tfoot>
          <tr className="report__total">
            <th scope="row" colSpan={5}>
              {sum === 0 ? tr("Balanced") : tr("Out of balance by")}
            </th>
            <td className="num">
              {currency && <Money amount={{ ...currency, minor: sum, text: String(sum) }} strong withCommodity />}
            </td>
          </tr>
        </tfoot>
      </table>

      <details className="valuation history-panel">
        <summary>
          {tr("History")}
          {history.data && history.data[0] && (
            <span className="muted">
              {" "}
              · {tr("last changed by {who}", { who: history.data[0].actor || "—" })}
            </span>
          )}
        </summary>
        {history.data && <HistoryTable changes={history.data} linkTransactions={false} />}
      </details>
    </article>
  );
}

function SplitRow({
  split,
  accountPath,
  tagNames,
  onMark,
  canFreeze,
  busy,
}: {
  split: Split;
  accountPath: string | undefined;
  tagNames: readonly string[];
  onMark?: ((state: ReconcileState) => void) | undefined;
  canFreeze?: boolean;
  busy?: boolean;
}) {
  const tr = useT();
  const foreign = split.quantity.commodity_id !== split.value.commodity_id;
  const price = split.price ?? split.implied_price;
  const frozen = split.reconcile_state === "FROZEN";
  return (
    <tr>
      <td>
        <Link to={`../accounts/${split.account_id}`} className="account-link">
          {accountPath ?? split.account_id}
        </Link>
        {split.action && <span className="muted"> · {split.action}</span>}
      </td>
      <td className="muted">
        {split.memo}
        {tagNames.length > 0 && (
          <span className="split-tags">
            {tagNames.map((name) => (
              <span key={name} className="tag-chip">
                {name}
              </span>
            ))}
          </span>
        )}
      </td>
      <td className="register__mark">
        {onMark && !frozen ? (
          <select
            className="reconcile-select"
            aria-label={tr("Reconciliation state")}
            value={split.reconcile_state}
            disabled={busy}
            onChange={(e) => onMark(e.target.value as ReconcileState)}
          >
            <option value="NOT_RECONCILED">{tr("Not reconciled")}</option>
            <option value="CLEARED">{tr("Cleared")}</option>
            <option value="RECONCILED">{tr("Reconciled")}</option>
            {/* Irreversible, so only offered to an owner -- and the server checks. */}
            {canFreeze && <option value="FROZEN">{tr("Frozen")}</option>}
          </select>
        ) : (
          <ReconcileMark state={split.reconcile_state} />
        )}
        {split.reconcile_date && <span className="muted"> {split.reconcile_date}</span>}
      </td>
      <td className="num">{foreign ? <Money amount={split.quantity} withCommodity /> : <span className="muted">—</span>}</td>
      <td className="num">
        {foreign && price ? (
          <span title={`${price.num}/${price.den}${split.price ? "" : " (implied)"}`}>{formatRate(price)}</span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="num">
        <Money amount={split.value} />
      </td>
    </tr>
  );
}

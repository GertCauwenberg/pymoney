import { formatRate } from "@pymoney/core";
import type { Split } from "@pymoney/core";
import { useAccounts, usePayees, useTransaction } from "@pymoney/core/hooks";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { useBookContext } from "../reports/useReportParams";
import { ReconcileMark } from "./ReconcileMark";

/**
 * One transaction, every leg. Read-only for now.
 *
 * Values are shown as stored: debits positive, credits negative, and they
 * sum to zero in the transaction currency -- that is what double entry means,
 * and a detail view is the one place not to hide it behind natural signs.
 */
export function TransactionPage() {
  const { bookId } = useBookContext();
  const { transactionId = "" } = useParams();
  const tx = useTransaction(bookId, transactionId);
  const accounts = useAccounts(bookId, { include_closed: true });
  const payees = usePayees(bookId);
  const navigate = useNavigate();

  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  const payeesById = useMemo(() => new Map((payees.data ?? []).map((p) => [p.id, p.name])), [payees.data]);

  if (tx.isPending) return <Loading label="Loading the transaction…" />;
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

  return (
    <article className="report-page transaction">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../reports/balance-sheet">Reports</Link> › Transaction
          </p>
          <h1>{payee ?? t.memo ?? "Transaction"}</h1>
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
          <button type="button" className="btn" onClick={() => window.print()}>
            Print
          </button>
        </div>
      </header>

      <dl className="facts">
        <div>
          <dt>Date</dt>
          <dd>{t.date}</dd>
        </div>
        <div>
          <dt>Payee</dt>
          <dd>{payee ?? <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>Memo</dt>
          <dd>{t.memo || <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>Number</dt>
          <dd>{t.number || <span className="muted">—</span>}</dd>
        </div>
        <div>
          <dt>Currency</dt>
          <dd>{t.currency}</dd>
        </div>
        <div>
          <dt>Reconciliation</dt>
          <dd>
            <ReconcileMark state={t.reconcile_state} /> {t.reconcile_state.toLowerCase().replace("_", " ")}
          </dd>
        </div>
        {t.entered_at && (
          <div>
            <dt>Entered</dt>
            <dd>{new Date(t.entered_at).toLocaleString()}</dd>
          </div>
        )}
        {t.imported_id && (
          <div>
            <dt>Imported as</dt>
            <dd>
              <code>{t.imported_id}</code>
            </dd>
          </div>
        )}
        <div>
          <dt>Revision</dt>
          <dd>{t.revision}</dd>
        </div>
      </dl>

      <h2>Splits</h2>
      <table className="report splits">
        <thead>
          <tr>
            <th scope="col">Account</th>
            <th scope="col">Memo</th>
            <th scope="col" className="register__mark" title="Reconciliation">
              R
            </th>
            <th scope="col" className="num">
              Quantity
            </th>
            <th scope="col" className="num">
              Price
            </th>
            <th scope="col" className="num">
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {t.splits.map((s) => (
            <SplitRow key={s.id} split={s} accountPath={accountsById.get(s.account_id)?.path} />
          ))}
        </tbody>
        <tfoot>
          <tr className="report__total">
            <th scope="row" colSpan={5}>
              {sum === 0 ? "Balanced" : "Out of balance by"}
            </th>
            <td className="num">
              {currency && <Money amount={{ ...currency, minor: sum, text: String(sum) }} strong withCommodity />}
            </td>
          </tr>
        </tfoot>
      </table>
    </article>
  );
}

function SplitRow({ split, accountPath }: { split: Split; accountPath: string | undefined }) {
  const foreign = split.quantity.commodity_id !== split.value.commodity_id;
  const price = split.price ?? split.implied_price;
  return (
    <tr>
      <td>
        <Link to={`../accounts/${split.account_id}`} className="account-link">
          {accountPath ?? split.account_id}
        </Link>
        {split.action && <span className="muted"> · {split.action}</span>}
      </td>
      <td className="muted">{split.memo}</td>
      <td className="register__mark">
        <ReconcileMark state={split.reconcile_state} />
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

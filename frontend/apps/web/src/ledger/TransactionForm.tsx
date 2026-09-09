import { useT } from "@pymoney/core/hooks";
import {
  balanceAmount,
  draftBalance,
  draftFromTransaction,
  draftToTransactionIn,
  emptyLeg,
  needsQuantity,
  newDraft,
  postableAccounts,
  today,
  minorToDecimal,
  validateDraft,
} from "@pymoney/core";
import type { Account, Commodity, DraftProblem, Payee, Transaction, TransactionDraft } from "@pymoney/core";
import { useApi, useCreatePayee, useCreateTransaction, useUpdateTransaction } from "@pymoney/core/hooks";
import { useMemo, useState, type FormEvent } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Money } from "../components/Money";
import { Field } from "../components/fields";

const NEW_PAYEE = "__new__";

/**
 * Post or edit a transaction.
 *
 * Amounts are typed as decimals in the transaction currency and sent as the
 * strings they are; nothing here is ever a float. Money *into* an account is
 * positive and money out of it negative, as the ledger stores it -- the
 * register applies the natural sign for display, a form that edits the
 * stored figures should not. One leg may be left blank: the server fills it
 * with whatever balances the entry, and the footer shows what that will be.
 *
 * A payee can be created on the spot: pick "New payee…", type the name, and
 * it is created just before the transaction is posted.
 */
export function TransactionForm({
  bookId,
  accounts,
  payees,
  commodities,
  currency,
  existing,
  defaultAccountId,
  onSaved,
  onCancel,
}: {
  bookId: string;
  accounts: readonly Account[];
  payees: readonly Payee[];
  commodities: readonly Commodity[];
  /** The transaction currency: the book's, or the existing transaction's. */
  currency: Commodity;
  /** Edit this transaction; omit to post a new one. */
  existing?: Transaction | undefined;
  /** For a new transaction: the account whose register we came from. */
  defaultAccountId?: string | undefined;
  onSaved: (saved: Transaction) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const api = useApi();
  const [draft, setDraft] = useState<TransactionDraft>(() =>
    existing ? draftFromTransaction(existing, currency.id) : newDraft(defaultAccountId ?? "", today()),
  );
  // The transaction currency is chosen when posting and fixed once a
  // transaction exists: its splits' values are already denominated in it, and
  // changing it would silently reinterpret every figure.
  const [currencyId, setCurrencyId] = useState(currency.id);
  const [payeeChoice, setPayeeChoice] = useState<string>(draft.payee_id ?? "");
  const [newPayeeName, setNewPayeeName] = useState("");
  const [touched, setTouched] = useState(false);
  // One key per form instance: a retry after a dropped connection posts once.
  const [idempotencyKey] = useState(() => `web-${Date.now()}-${Math.random().toString(36).slice(2)}`);

  const create = useCreateTransaction(bookId);
  const update = useUpdateTransaction(bookId);
  const createPayee = useCreatePayee(bookId);

  const accountsById = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const commoditiesById = useMemo(() => new Map(commodities.map((c) => [c.id, c])), [commodities]);
  const postable = useMemo(() => postableAccounts(accounts), [accounts]);
  const sortedPayees = useMemo(() => [...payees].sort((a, b) => a.name.localeCompare(b.name)), [payees]);
  const currencies = useMemo(
    () => commodities.filter((c) => c.kind === "CURRENCY").sort((a, b) => a.code.localeCompare(b.code)),
    [commodities],
  );
  const txCurrency = (existing ? undefined : commoditiesById.get(currencyId)) ?? currency;

  const problems = validateDraft(draft, accountsById, txCurrency, commoditiesById);

  /**
   * Choosing a payee fills in what is predictable, never what is typed:
   * the category leg from the payee's default account, and the amount of its
   * last transaction on this account -- both only into blank fields, so a
   * half-filled form is never overwritten. Two clicks fewer on the most
   * repeated action there is.
   */
  const prefillFromPayee = async (payeeId: string) => {
    if (!payeeId || payeeId === NEW_PAYEE || existing) return;
    const payee = payees.find((p) => p.id === payeeId);
    if (payee?.default_account_id && accountsById.has(payee.default_account_id)) {
      setDraft((d) => {
        const blank = d.legs.findIndex((leg, i) => i > 0 && !leg.account_id);
        if (blank < 0) return d;
        return { ...d, legs: d.legs.map((leg, i) => (i === blank ? { ...leg, account_id: payee.default_account_id! } : leg)) };
      });
    }
    try {
      const last = await api.searchTransactions(bookId, { payee_id: payeeId, limit: 1 });
      const previous = last.items[0];
      if (!previous) return;
      const split = previous.splits.find((sp) => sp.account_id === defaultAccountId) ?? previous.splits[0];
      if (!split || split.value.commodity_id !== txCurrency.id) return;
      setDraft((d) => {
        const first = d.legs[0];
        if (!first || first.value.trim() !== "" || first.account_id !== split.account_id) return d;
        return { ...d, legs: d.legs.map((leg, i) => (i === 0 ? { ...leg, value: minorToDecimal(split.value.minor, split.value.scale) } : leg)) };
      });
    } catch {
      // A prefill is a convenience; a failed lookup is not an error.
    }
  };
  const balance = draftBalance(draft, txCurrency);
  const creatingPayee = payeeChoice === NEW_PAYEE;
  const payeeProblem = creatingPayee && newPayeeName.trim() === "" ? t("Give the new payee a name.") : null;
  const showQuantity = draft.legs.some((leg) => needsQuantity(leg, accountsById, txCurrency.id));
  const busy = create.isPending || update.isPending || createPayee.isPending;
  const error = create.error ?? update.error ?? createPayee.error;

  const setLeg = (index: number, patch: Partial<TransactionDraft["legs"][number]>) =>
    setDraft((d) => ({ ...d, legs: d.legs.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)) }));
  const removeLeg = (index: number) => setDraft((d) => ({ ...d, legs: d.legs.filter((_, i) => i !== index) }));
  const addLeg = () => setDraft((d) => ({ ...d, legs: [...d.legs, emptyLeg()] }));

  const problemsFor = (leg: number | null): DraftProblem[] => problems.filter((p) => p.leg === leg);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (problems.length > 0 || payeeProblem) return;
    let payeeId: string | null = payeeChoice === "" || creatingPayee ? null : payeeChoice;
    try {
      if (creatingPayee) {
        const payee = await createPayee.mutateAsync({
          name: newPayeeName.trim(),
          notes: "",
          address: "",
          match_keys: [],
          default_account_id: null,
        });
        payeeId = payee.id;
        // Should the post below fail, the payee exists: keep it selected so a
        // retry does not try to create it twice.
        setPayeeChoice(payee.id);
      }
      const body = draftToTransactionIn({ ...draft, payee_id: payeeId }, accountsById, txCurrency);
      const saved = existing
        ? await update.mutateAsync({
            transactionId: existing.id,
            body: { date: body.date, legs: body.legs, memo: body.memo, payee_id: body.payee_id ?? null, number: body.number },
          })
        : await create.mutateAsync({ body, idempotencyKey });
      onSaved(saved);
    } catch {
      // Shown through the mutation's error below.
    }
  };

  return (
    <form className="card transaction-form" onSubmit={(e) => void submit(e)} aria-label={existing ? t("Edit transaction") : t("New transaction")}>
      <h2>{existing ? t("Edit transaction") : t("New transaction")}</h2>
      {error && <ErrorBanner error={error} title={existing ? t("Could not save") : t("Could not post")} />}

      <div className="field-row">
        <Field label={t("Date")}>
          <input type="date" required value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
        </Field>
        <Field label={t("Payee")}>
          <select
            value={payeeChoice}
            onChange={(e) => {
              setPayeeChoice(e.target.value);
              if (e.target.value !== NEW_PAYEE) setNewPayeeName("");
              void prefillFromPayee(e.target.value);
            }}
          >
            <option value="">—</option>
            {sortedPayees.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
            <option value={NEW_PAYEE}>{t("New payee…")}</option>
          </select>
        </Field>
        {creatingPayee && (
          <Field label={t("New payee name")}>
            <input type="text" required autoFocus value={newPayeeName} onChange={(e) => setNewPayeeName(e.target.value)} />
          </Field>
        )}
        <Field label={t("Memo")}>
          <input type="text" value={draft.memo} onChange={(e) => setDraft({ ...draft, memo: e.target.value })} />
        </Field>
        <Field label={t("Number")} hint={t("Cheque or reference")}>
          <input type="text" value={draft.number} onChange={(e) => setDraft({ ...draft, number: e.target.value })} className="input--short" />
        </Field>
        <Field label={t("Currency")} hint={existing ? undefined : t("Fixed once the transaction is posted.")}>
          {existing ? (
            <input type="text" value={txCurrency.code} disabled className="input--short" />
          ) : (
            <select value={currencyId} onChange={(e) => setCurrencyId(e.target.value)} className="input--short">
              {currencies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id === currency.id ? t("{code} (book currency)", { code: c.code }) : c.code}
                </option>
              ))}
            </select>
          )}
        </Field>
      </div>

      <table className="report transaction-form__legs">
        <thead>
          <tr>
            <th scope="col">{t("Account")}</th>
            <th scope="col">{t("Leg memo")}</th>
            {showQuantity && (
              <th scope="col" className="num">
                {t("Quantity")}
              </th>
            )}
            <th scope="col" className="num">
              Amount ({txCurrency.code})
            </th>
            <th scope="col" />
          </tr>
        </thead>
        <tbody>
          {draft.legs.map((leg, i) => {
            const account = accountsById.get(leg.account_id);
            const legProblems = touched ? problemsFor(i) : [];
            return (
              <tr key={leg.key} className={legProblems.length ? "transaction-form__leg--invalid" : undefined}>
                <td>
                  <select
                    aria-label={t("Account of leg {n}", { n: i + 1 })}
                    value={leg.account_id}
                    onChange={(e) => setLeg(i, { account_id: e.target.value })}
                  >
                    <option value="">{t("Choose an account…")}</option>
                    {account && (account.placeholder || account.closed) && (
                      <option value={account.id} disabled>
                        {account.path}
                      </option>
                    )}
                    {postable.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.path}
                        {a.commodity_id !== txCurrency.id ? ` (${a.commodity})` : ""}
                      </option>
                    ))}
                  </select>
                  {legProblems.map((p) => (
                    <div key={p.message} className="field__error" role="alert">
                      {p.message}
                    </div>
                  ))}
                </td>
                <td>
                  <input
                    type="text"
                    aria-label={t("Memo of leg {n}", { n: i + 1 })}
                    value={leg.memo}
                    onChange={(e) => setLeg(i, { memo: e.target.value })}
                  />
                </td>
                {showQuantity && (
                  <td className="num">
                    {account && needsQuantity(leg, accountsById, txCurrency.id) ? (
                      <input
                        type="text"
                        inputMode="decimal"
                        className="input--amount"
                        aria-label={t("Quantity of leg {n} in {commodity}", { n: i + 1, commodity: account.commodity })}
                        placeholder={account.commodity}
                        value={leg.quantity}
                        onChange={(e) => setLeg(i, { quantity: e.target.value })}
                      />
                    ) : null}
                  </td>
                )}
                <td className="num">
                  <input
                    type="text"
                    inputMode="decimal"
                    className="input--amount"
                    aria-label={t("Amount of leg {n}", { n: i + 1 })}
                    placeholder={balance.blank && leg.value === "" ? "balances" : "0.00"}
                    value={leg.value}
                    onChange={(e) => setLeg(i, { value: e.target.value })}
                  />
                </td>
                <td className="num">
                  <button
                    type="button"
                    className="btn btn--small"
                    aria-label={t("Remove leg {n}", { n: i + 1 })}
                    disabled={draft.legs.length <= 2}
                    onClick={() => removeLeg(i)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={showQuantity ? 3 : 2}>
              <button type="button" className="btn btn--small" onClick={addLeg}>
                {t("Add leg")}
              </button>
            </td>
            <td className="num muted">
              {balance.blank ? (
                <>
                  {t("blank leg gets")} <Money amount={balanceAmount(-balance.sum, txCurrency)} />
                </>
              ) : balance.sum === 0n ? (
                "balanced"
              ) : (
                <span className="money--negative">
                  {t("off by")} <Money amount={balanceAmount(balance.sum, txCurrency)} />
                </span>
              )}
            </td>
            <td />
          </tr>
        </tfoot>
      </table>
      <p className="muted transaction-form__hint">
        {t("Money into an account is positive, money out of it negative. Leave one amount blank and it takes whatever balances the entry.")}
      </p>

      {touched &&
        [...problemsFor(null), ...(payeeProblem ? [{ leg: null, message: payeeProblem }] : [])].map((p) => (
          <div key={p.message} className="banner banner--warning" role="alert">
            {p.message}
          </div>
        ))}

      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={busy}>
          {busy ? "…" : existing ? t("Save") : t("Post")}
        </button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          {t("Cancel")}
        </button>
      </div>
    </form>
  );
}

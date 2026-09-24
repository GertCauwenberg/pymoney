import { useT } from "@pymoney/core/hooks";
import {
  balanceAmount,
  draftBalance,
  draftFromTransaction,
  newDraftFromTransaction,
  draftToTransactionIn,
  emptyLeg,
  needsQuantity,
  newDraft,
  postableAccounts,
  today,
  minorToDecimal,
  validateDraft,
} from "@pymoney/core";
import type { Account, Commodity, DraftProblem, LegDraft, Payee, Tag, Transaction, TransactionDraft } from "@pymoney/core";
import { useAddAttachment, useApi, useCreatePayee, useCreateTag, useCreateTransaction, useTags, useUpdateTransaction } from "@pymoney/core/hooks";
import { useId, useMemo, useRef, useState, type SubmitEvent, type KeyboardEvent } from "react";

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
  cloning,
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
  cloning?: Transaction | undefined;
  /** For a new transaction: the account whose register we came from. */
  defaultAccountId?: string | undefined;
  onSaved: (saved: Transaction) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const api = useApi();
  const [draft, setDraft] = useState<TransactionDraft>(() =>
    existing ? draftFromTransaction(existing, currency.id) : 
    cloning ? newDraftFromTransaction(cloning, currency.id, today()) : newDraft(defaultAccountId ?? "", today()),
  );
  // The transaction currency is chosen when posting and fixed once a
  // transaction exists: its splits' values are already denominated in it, and
  // changing it would silently reinterpret every figure.
  const [currencyId, setCurrencyId] = useState(currency.id);
  const [payeeChoice, setPayeeChoice] = useState<string>(draft.payee_id ?? "");
  const [newPayeeName, setNewPayeeName] = useState(() => {
    const selected = payees.find((p) => p.id === draft.payee_id);
    return selected?.name ?? "";
  });
  const [touched, setTouched] = useState(false);
  // One key per form instance: a retry after a dropped connection posts once.
  const [idempotencyKey] = useState(() => `web-${Date.now()}-${Math.random().toString(36).slice(2)}`);

  const create = useCreateTransaction(bookId);
  const update = useUpdateTransaction(bookId);
  const createPayee = useCreatePayee(bookId);
  const createTag = useCreateTag(bookId);
  const tags = useTags(bookId).data ?? [];
  const addAttachment = useAddAttachment(bookId);

  // Receipts picked while posting. There is no transaction id to attach them
  // to yet, so they are held here and uploaded the instant POST hands one
  // back. (When editing, the transaction already exists -- attachments have a
  // panel of their own on its page, and this stays empty.)
  const [receipts, setReceipts] = useState<File[]>([]);
  const [receiptError, setReceiptError] = useState<unknown>(null);
  const receiptInput = useRef<HTMLInputElement>(null);

  const accountsById = useMemo(() => new Map(accounts.map((a) => [a.id, a])), [accounts]);
  const commoditiesById = useMemo(() => new Map(commodities.map((c) => [c.id, c])), [commodities]);
  const tagsById = useMemo(() => new Map(tags.map((tg) => [tg.id, tg])), [tags]);
  const tagIdByName = useMemo(() => new Map(tags.map((tg) => [tg.name.toLowerCase(), tg.id])), [tags]);
  const postable = useMemo(() => postableAccounts(accounts), [accounts]);
  const sortedPayees = useMemo(() => [...payees].sort((a, b) => a.name.localeCompare(b.name)), [payees]);
  const payeeByName = useMemo(
    () => new Map(payees.map((p) => [p.name.toLowerCase(), p])),
    [payees],
  );
  const sortedTags = useMemo(() => [...tags].sort((a, b) => a.name.localeCompare(b.name)), [tags]);
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
  const busy = create.isPending || update.isPending || createPayee.isPending || createTag.isPending || addAttachment.isPending;
  const error = create.error ?? update.error ?? createPayee.error ?? createTag.error;

  const setLeg = (index: number, patch: Partial<TransactionDraft["legs"][number]>) =>
    setDraft((d) => ({ ...d, legs: d.legs.map((leg, i) => (i === index ? { ...leg, ...patch } : leg)) }));
  const removeLeg = (index: number) => setDraft((d) => ({ ...d, legs: d.legs.filter((_, i) => i !== index) }));
  const addLeg = () => setDraft((d) => ({ ...d, legs: [...d.legs, emptyLeg()] }));

  /** Put a typed name on a leg: an existing tag by id, a new one by name,
   * never the same tag twice. */
  const addTag = (index: number, raw: string) => {
    const name = raw.trim();
    if (!name) return;
    setLeg(index, addTagToLeg(draft.legs[index]!, name, tagIdByName));
  };
  const removeTag = (index: number, ref: { id?: string; name?: string }) => {
    const leg = draft.legs[index]!;
    setLeg(index, {
      tag_ids: ref.id ? leg.tag_ids.filter((id) => id !== ref.id) : leg.tag_ids,
      new_tag_names: ref.name ? leg.new_tag_names.filter((n) => n !== ref.name) : leg.new_tag_names,
    });
  };

  const problemsFor = (leg: number | null): DraftProblem[] => problems.filter((p) => p.leg === leg);

  /** Create every tag typed but not yet in the book (once per distinct name),
   * and return a draft whose legs name only ids. */
  const materialiseTags = async (source: TransactionDraft): Promise<TransactionDraft> => {
    const fresh = new Map<string, string>();
    for (const leg of source.legs) {
      for (const name of leg.new_tag_names) {
        const key = name.toLowerCase();
        if (tagIdByName.has(key) || fresh.has(key)) continue;
        const tag = await createTag.mutateAsync({ name, notes: "", colour: null, closed: false });
        fresh.set(key, tag.id);
      }
    }
    const idOf = (name: string) => tagIdByName.get(name.toLowerCase()) ?? fresh.get(name.toLowerCase())!;
    return {
      ...source,
      legs: source.legs.map((leg) => ({
        ...leg,
        tag_ids: [...new Set([...leg.tag_ids, ...leg.new_tag_names.map(idOf)])],
        new_tag_names: [],
      })),
    };
  };

  const submit = async (e: SubmitEvent) => {
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
      const resolved = await materialiseTags(draft);
      if (resolved !== draft) setDraft(resolved);
      const body = draftToTransactionIn({ ...resolved, payee_id: payeeId }, accountsById, txCurrency);
      // The POST is idempotent (one key per form instance), so re-submitting
      // after a failed upload returns this same transaction rather than a
      // second one.
      const saved = existing
        ? await update.mutateAsync({
            transactionId: existing.id,
            body: { date: body.date, legs: body.legs, memo: body.memo, payee_id: body.payee_id ?? null, number: body.number },
          })
        : await create.mutateAsync({ body, idempotencyKey });
      setReceiptError(null);
      if (!existing && receipts.length > 0) {
        // The transaction exists now, so its id is real. Upload the staged
        // receipts, keeping any that fail so a retry only re-sends those.
        const failed: File[] = [];
        let lastError: unknown = null;
        for (const file of receipts) {
          try { 
            await addAttachment.mutateAsync({ transactionId: saved.id, file });
          } catch (uploadError) {
            failed.push(file);
            lastError = uploadError;
          }
        }  
        setReceipts(failed);
        if (receiptInput.current) receiptInput.current.value = "";
        if (failed.length > 0) {
          setReceiptError(lastError);
          return; // stay open: the transaction is saved, the receipt is not
        }
      }
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
          <input
            type="text"
            list="payee-suggestions"
            value={newPayeeName}
            placeholder={t("Choose or type a payee…")}
            onChange={(e) => {
              const name = e.target.value;
              setNewPayeeName(name);
              const payee = payeeByName.get(name.trim().toLowerCase());
              if (payee) {
                setPayeeChoice(payee.id);
                void prefillFromPayee(payee.id);
              } else {
                setPayeeChoice(name.trim() ? NEW_PAYEE : "");
              }
            }}
            onBlur={() => {
              const payee = payeeByName.get(newPayeeName.trim().toLowerCase());
              if (payee) {
                setPayeeChoice(payee.id);
                void prefillFromPayee(payee.id);
              }
            }}
          />
          <datalist id="payee-suggestions">
            {sortedPayees.map((p) => (
              <option key={p.id} value={p.name} />
            ))}
          </datalist>
        </Field>
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
                  {c.code}
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
            <th scope="col">{t("Tags")}</th>
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
                  <AccountAutocomplete
                    leg={i}
                    account={account}
                    postable={postable}
                    txCurrency={txCurrency}
                    onChange={(accountId) => setLeg(i, { account_id: accountId })}
                  />
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
                <td>
                  <LegTags
                    leg={i}
                    value={leg}
                    tagsById={tagsById}
                    suggestions={sortedTags}
                    onAdd={(name) => addTag(i, name)}
                    onRemove={(ref) => removeTag(i, ref)}
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
            <td colSpan={showQuantity ? 4 : 3}>
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

      {!existing && (
        <Field
          label={t("Receipts")}
          hint={receipts.length > 0 ? receipts.map((f) => f.name).join(", ") : t("Photos or a PDF; attached once the transaction is posted.")}
        >
          <input
            ref={receiptInput}
            type="file"
            multiple
            accept="image/*,application/pdf"
            onChange={(e) => {
              setReceipts(Array.from(e.target.files ?? []));
              setReceiptError(null);
            }}
          />
        </Field>
      )}
      {receiptError != null && <ErrorBanner error={receiptError} title={t("The transaction was posted, but a receipt did not upload")} />}

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

function AccountAutocomplete({
  leg,
  account,
  postable,
  txCurrency,
  onChange,
}: {
  leg: number;
  account: Account | undefined;
  postable: readonly Account[];
  txCurrency: Commodity;
  onChange: (accountId: string) => void;
}) {
  const t = useT();
  const listId = useId();
  const [text, setText] = useState(() => account?.path ?? "");

  const accounts = useMemo(() => {
    const byId = new Map(postable.map((a) => [a.id, a]));
    if (account && (account.placeholder || account.closed)) byId.set(account.id, account);
    return [...byId.values()];
  }, [account, postable]);

  const selectAccount = (value: string) => {
    const selected = accounts.find((a) => a.path === value);
    if (selected && !(selected.placeholder || selected.closed)) {
      onChange(selected.id);
      setText(selected.path);
    } else if (value === "") {
      onChange("");
      setText("");
    } else {
      // Keep the current valid selection if the typed text does not identify
      // an existing postable account.
      setText(account?.path ?? "");
    }
  };

  return (
    <>
      <input
        type="text"
        list={listId}
        aria-label={t("Account of leg {n}", { n: leg + 1 })}
        placeholder={t("Choose an account…")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => selectAccount(text)}
      />
      <datalist id={listId}>
        {accounts.map((a) => (
          <option
            key={a.id}
            value={a.path}
            disabled={a.placeholder || a.closed}
          >
            {a.commodity_id !== txCurrency.id ? ` (${a.commodity})` : ""}
          </option>
        ))}
      </datalist>
    </>
  );
}

/** Resolve a typed name to an edit of a leg: an existing tag becomes an id, a
 * new one a name, and neither is added twice. Exported-shaped but kept local. */
function addTagToLeg(leg: LegDraft, name: string, idByName: ReadonlyMap<string, string>): Partial<LegDraft> {
  const existingId = idByName.get(name.toLowerCase());
  if (existingId) {
    return leg.tag_ids.includes(existingId) ? {} : { tag_ids: [...leg.tag_ids, existingId] };
  }
  return leg.new_tag_names.some((n) => n.toLowerCase() === name.toLowerCase()) ? {} : { new_tag_names: [...leg.new_tag_names, name] };
}

/** The tags on one leg: a removable chip per tag, plus an autocompleting
 * input. Typing a name the book does not have yet creates it on save. */
function LegTags({
  leg,
  value,
  tagsById,
  suggestions,
  onAdd,
  onRemove,
}: {
  leg: number;
  value: LegDraft;
  tagsById: ReadonlyMap<string, Tag>;
  suggestions: readonly Tag[];
  onAdd: (name: string) => void;
  onRemove: (ref: { id?: string; name?: string }) => void;
}) {
  const t = useT();
  const listId = useId();
  const [text, setText] = useState("");
  const commit = () => {
    onAdd(text);
    setText("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit();
    } else if (e.key === "Backspace" && text === "") {
      const last = value.new_tag_names.at(-1);
      if (last !== undefined) onRemove({ name: last });
      else if (value.tag_ids.length) onRemove({ id: value.tag_ids.at(-1)! });
    }
  };
  const chips: { key: string; label: string; fresh: boolean; ref: { id?: string; name?: string } }[] = [
    ...value.tag_ids.map((id) => ({ key: id, label: tagsById.get(id)?.name ?? "?", fresh: false, ref: { id } })),
    ...value.new_tag_names.map((name) => ({ key: `new:${name}`, label: name, fresh: true, ref: { name } })),
  ];
  return (
    <div className="tag-field">
      {chips.map((chip) => (
        <span key={chip.key} className={chip.fresh ? "tag-chip tag-chip--new" : "tag-chip"}>
          {chip.label}
          <button type="button" className="tag-chip__remove" aria-label={t("Remove tag {name}", { name: chip.label })} onClick={() => onRemove(chip.ref)}>
            ×
          </button>
        </span>
      ))}
      <input
        type="text"
        className="tag-field__input"
        list={listId}
        aria-label={t("Tags of leg {n}", { n: leg + 1 })}
        placeholder={chips.length === 0 ? t("Add a tag…") : ""}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={commit}
      />
      <datalist id={listId}>
        {suggestions.map((tag) => (
          <option key={tag.id} value={tag.name} />
        ))}
      </datalist>
    </div>
  );
}

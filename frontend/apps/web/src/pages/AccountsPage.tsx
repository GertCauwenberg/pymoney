import { useT } from "@pymoney/core/hooks";
import type { Account, AccountClass, AccountType, Commodity } from "@pymoney/core";
import { useAccounts, useChangeAccount, useCommodities } from "@pymoney/core/hooks";
import { useEffect, useMemo, useState, type SubmitEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field, Toggle } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

/** Every type, under the class it belongs to; the server enforces the pairing. */
const TYPES: { cls: AccountClass; label: string; types: { id: AccountType; label: string }[] }[] = [
  {
    cls: "ASSET",
    label: "Assets",
    types: [
      { id: "CHECKING", label: "Checking" },
      { id: "SAVINGS", label: "Savings" },
      { id: "CASH", label: "Cash" },
      { id: "INVESTMENT", label: "Investment" },
      { id: "STOCK", label: "Stock / security" },
      { id: "RECEIVABLE", label: "Receivable" },
      { id: "ASSET", label: "Other asset" },
    ],
  },
  {
    cls: "LIABILITY",
    label: "Liabilities",
    types: [
      { id: "CREDIT_CARD", label: "Credit card" },
      { id: "LOAN", label: "Loan" },
      { id: "PAYABLE", label: "Payable" },
      { id: "LIABILITY", label: "Other liability" },
    ],
  },
  { cls: "INCOME", label: "Income", types: [{ id: "INCOME", label: "Income" }] },
  { cls: "EXPENSE", label: "Expenses", types: [{ id: "EXPENSE", label: "Expense" }] },
  { cls: "EQUITY", label: "Equity", types: [{ id: "EQUITY", label: "Equity" }] },
];

export function classOfType(type: AccountType): AccountClass {
  return TYPES.find((g) => g.types.some((t) => t.id === type))!.cls;
}

/** Depth-first by path, so a parent always precedes its children. */
export function orderedTree(accounts: readonly Account[]): { account: Account; depth: number }[] {
  const byParent = new Map<string | null, Account[]>();
  for (const a of accounts) {
    const list = byParent.get(a.parent_id ?? null) ?? [];
    list.push(a);
    byParent.set(a.parent_id ?? null, list);
  }
  const out: { account: Account; depth: number }[] = [];
  const visit = (parent: string | null, depth: number) => {
    for (const a of (byParent.get(parent) ?? []).sort((x, y) => x.name.localeCompare(y.name))) {
      out.push({ account: a, depth });
      visit(a.id, depth + 1);
    }
  };
  visit(null, 0);
  return out;
}

function descendantIds(accounts: readonly Account[], rootId: string): Set<string> {
  const out = new Set<string>([rootId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const a of accounts) {
      if (a.parent_id && out.has(a.parent_id) && !out.has(a.id)) {
        out.add(a.id);
        grew = true;
      }
    }
  }
  return out;
}

/**
 * The chart of accounts. Everyone can browse it; only an owner gets the
 * controls, because renaming, moving or deleting an account reshapes every
 * report the household shares -- the server answers 403 to anyone else.
 */
export function AccountsPage() {
  const t = useT();
  const { bookId, baseCurrencyId, role } = useBookContext();
  const accounts = useAccounts(bookId, { include_closed: true });
  const commodities = useCommodities(bookId);
  const [search, setSearch] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const editing = search.get("edit");
  const owner = role === "owner";

  const rows = useMemo(() => orderedTree(accounts.data ?? []), [accounts.data]);
  const editingAccount = accounts.data?.find((a) => a.id === editing);
  const setEditing = (id: string | null) =>
    setSearch(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (id) next.set("edit", id);
        else next.delete("edit");
        return next;
      },
      { replace: true },
    );

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../../reports/balance-sheet">{t("Reports")}</Link> › {t("Settings")} › {t("Accounts")}
          </p>
          <h1>{t("Accounts")}</h1>
          <p className="muted">
            {owner
              ? t("The chart of accounts. Changes here reshape every report, so only owners may make them.")
              : t("The chart of accounts. Only the book's owners can change it.")}
          </p>
        </div>
        <div className="actions">
          <Toggle label={t("Show closed")} checked={showClosed} onChange={setShowClosed} />
          {owner && !creating && (
            <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
              {t("New account")}
            </button>
          )}
        </div>
      </header>

      {accounts.isPending && <Loading />}
      {accounts.isError && <ErrorBanner error={accounts.error} onRetry={() => void accounts.refetch()} />}

      {creating && accounts.data && commodities.data && (
        <AccountEditor
          bookId={bookId}
          accounts={accounts.data}
          commodities={commodities.data}
          baseCurrencyId={baseCurrencyId}
          onDone={() => setCreating(false)}
        />
      )}
      {owner && editingAccount && accounts.data && commodities.data && (
        <AccountEditor
          bookId={bookId}
          accounts={accounts.data}
          commodities={commodities.data}
          baseCurrencyId={baseCurrencyId}
          existing={editingAccount}
          onDone={() => setEditing(null)}
        />
      )}

      {accounts.data && (
        <table className="report accounts">
          <thead>
            <tr>
              <th scope="col">{t("Account")}</th>
              <th scope="col">{t("Type")}</th>
              <th scope="col">{t("Commodity")}</th>
              <th scope="col">{t("Notes")}</th>
              {owner && <th scope="col" />}
            </tr>
          </thead>
          <tbody>
            {rows
              .filter(({ account }) => showClosed || !account.closed)
              .map(({ account, depth }) => (
                <AccountRow
                  key={account.id}
                  account={account}
                  depth={depth}
                  owner={owner}
                  bookId={bookId}
                  onEdit={() => setEditing(account.id)}
                />
              ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={owner ? 5 : 4} className="empty">
                  No accounts yet.{owner ? " Create the first one above." : ""}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </article>
  );
}

function AccountRow({
  account,
  depth,
  owner,
  bookId,
  onEdit,
}: {
  account: Account;
  depth: number;
  owner: boolean;
  bookId: string;
  onEdit: () => void;
}) {
  const t = useT();
  const change = useChangeAccount(bookId);
  const [confirming, setConfirming] = useState(false);
  const flags = [account.placeholder && "placeholder", account.closed && "closed"].filter(Boolean).join(", ");
  return (
    <tr className={account.closed ? "accounts__row--closed" : undefined}>
      <td>
        <span className="indent" style={{ ["--depth" as string]: depth }}>
          <span className="disclosure disclosure--leaf" aria-hidden="true" />
          <Link to={`../../accounts/${account.id}`} className="account-link" title={account.path}>
            {account.name}
          </Link>
          {flags && <span className="muted"> · {flags}</span>}
        </span>
      </td>
      <td className="muted">{account.type.toLowerCase().replace("_", " ")}</td>
      <td className="muted">{account.commodity}</td>
      <td className="muted">{account.description}</td>
      {owner && (
        <td className="num">
          {change.error && <ErrorBanner error={change.error} title={t("Could not delete")} />}
          {confirming ? (
            <span className="actions actions--inline">
              <span className="muted">Delete {account.name}?</span>
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={change.isPending}
                onClick={() => change.mutate({ kind: "delete", accountId: account.id }, { onSettled: () => setConfirming(false) })}
              >
                {t("Yes, delete")}
              </button>
              <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
                {t("No")}
              </button>
            </span>
          ) : (
            <span className="actions actions--inline">
              <button type="button" className="btn btn--small" onClick={onEdit} aria-label={`Edit ${account.name}`}>
                {t("Edit")}
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger"
                onClick={() => setConfirming(true)}
                aria-label={`Delete ${account.name}`}
                title={t("Only an account with no postings and no sub-accounts can be deleted; merge or move them first.")}
              >
                {t("Delete")}
              </button>
            </span>
          )}
        </td>
      )}
    </tr>
  );
}

/**
 * Create an account, or edit one. On edit, each kind of change goes to its
 * own endpoint -- rename, move, close, metadata -- in that order, and stops
 * at the first the server refuses, so what did go through is consistent.
 */
function AccountEditor({
  bookId,
  accounts,
  commodities,
  baseCurrencyId,
  existing,
  onDone,
}: {
  bookId: string;
  accounts: readonly Account[];
  commodities: readonly Commodity[];
  baseCurrencyId: string | null;
  existing?: Account;
  onDone: () => void;
}) {
  const t = useT();
  const change = useChangeAccount(bookId);
  const [name, setName] = useState(existing?.name ?? "");
  const [type, setType] = useState<AccountType>(existing?.type ?? "CHECKING");
  const [parentId, setParentId] = useState<string>(existing?.parent_id ?? "");
  const [commodityId, setCommodityId] = useState<string>(existing?.commodity_id ?? baseCurrencyId ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [code, setCode] = useState(existing?.code ?? "");
  const [institution, setInstitution] = useState(existing?.institution ?? "");
  const [iban, setIban] = useState(existing?.iban ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [placeholder, setPlaceholder] = useState(existing?.placeholder ?? false);
  const [closed, setClosed] = useState(existing?.closed ?? false);
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmingMerge, setConfirmingMerge] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Editing a different account: start again from its fields.
  useEffect(() => {
    if (!existing) return;
    setName(existing.name);
    setType(existing.type);
    setParentId(existing.parent_id ?? "");
    setCommodityId(existing.commodity_id);
    setDescription(existing.description);
    setCode(existing.code);
    setInstitution(existing.institution);
    setIban(existing.iban);
    setNotes(existing.notes);
    setPlaceholder(existing.placeholder);
    setClosed(existing.closed);
    setMergeTarget("");
    setConfirmingMerge(false);
    setError(null);
  }, [existing]);

  const cls = classOfType(type);
  const excluded = existing ? descendantIds(accounts, existing.id) : new Set<string>();
  const parents = accounts
    .filter((a) => a.account_class === cls && !excluded.has(a.id) && !a.closed)
    .sort((a, b) => a.path.localeCompare(b.path));
  const byId = new Map(accounts.map((a) => [a.id, a]));
  // Siblings with the same name are refused by the server; say so first.
  const clash = accounts.find(
    (a) => a.id !== existing?.id && (a.parent_id ?? "") === parentId && a.name.trim().toLowerCase() === name.trim().toLowerCase(),
  );

  // Merge candidates: the server's rules, said up front -- same class, same
  // commodity, not itself or a descendant, and able to take the postings.
  const mergeTargets = existing
    ? accounts
        .filter(
          (a) =>
            a.id !== existing.id &&
            a.account_class === existing.account_class &&
            a.commodity_id === existing.commodity_id &&
            !excluded.has(a.id) &&
            !a.placeholder &&
            !a.closed,
        )
        .sort((a, b) => a.path.localeCompare(b.path))
    : [];

  const merge = async () => {
    if (!existing || !mergeTarget) return;
    setError(null);
    try {
      await change.mutateAsync({ kind: "merge", sourceId: existing.id, targetId: mergeTarget });
      onDone();
    } catch (err) {
      setConfirmingMerge(false);
      setError(err);
    }
  };

  const submit = async (e: SubmitEvent) => {
    e.preventDefault();
    setError(null);
    try {
      if (!existing) {
        await change.mutateAsync({
          kind: "create",
          body: {
            name: name.trim(),
            type,
            parent_id: parentId || null,
            commodity_id: commodityId || null,
            description: description.trim(),
            code: code.trim(),
            placeholder,
            notes: notes.trim(),
            institution: institution.trim(),
            iban: iban.trim(),
          },
        });
      } else {
        if (name.trim() !== existing.name) await change.mutateAsync({ kind: "rename", accountId: existing.id, name: name.trim() });
        if ((parentId || null) !== (existing.parent_id ?? null)) {
          await change.mutateAsync({ kind: "move", accountId: existing.id, parentId: parentId || null });
        }
        const patch: Record<string, string | boolean> = {};
        if (description.trim() !== existing.description) patch["description"] = description.trim();
        if (code.trim() !== existing.code) patch["code"] = code.trim();
        if (notes.trim() !== existing.notes) patch["notes"] = notes.trim();
        if (institution.trim() !== existing.institution) patch["institution"] = institution.trim();
        if (iban.trim() !== existing.iban) patch["iban"] = iban.trim();
        if (placeholder !== existing.placeholder) patch["placeholder"] = placeholder;
        if (Object.keys(patch).length > 0) await change.mutateAsync({ kind: "patch", accountId: existing.id, body: patch });
        if (closed !== existing.closed) await change.mutateAsync({ kind: "closed", accountId: existing.id, closed });
      }
      onDone();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <form className="card account-editor" onSubmit={(e) => void submit(e)} aria-label={existing ? `Edit ${existing.name}` : t("New account")}>
      <h2>{existing ? `Edit ${existing.path}` : t("New account")}</h2>
      {error !== null && <ErrorBanner error={error} title={t("Could not save")} />}
      <div className="field-row">
        <Field label={t("Name")} hint={clash ? `${clash.path} already exists here.` : "No colons: they separate the path."}>
          <input type="text" required value={name} onChange={(e) => setName(e.target.value.replace(/:/g, ""))} />
        </Field>
        <Field
          label={t("Type")}
          hint={existing ? t("The type cannot be changed once the account exists.") : t("Decides which tree the account belongs to.")}
        >
          <select value={type} disabled={!!existing} onChange={(e) => setType(e.target.value as AccountType)}>
            {TYPES.map((g) => (
              <optgroup key={g.cls} label={g.label}>
                {g.types.map((ty) => (
                  <option key={ty.id} value={ty.id}>
                    {t(ty.label)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </Field>
        <Field label={t("Parent")} hint={t("Only {class} accounts can be the parent.", { class: t(TYPES.find((g) => g.cls === cls)!.label).toLowerCase() })}>
          <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">— top level —</option>
            {parents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.path}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label={t("Commodity")}
          hint={existing ? "Fixed once the account exists; create a new account to hold something else." : undefined}
        >
          <select value={commodityId} disabled={!!existing} onChange={(e) => setCommodityId(e.target.value)}>
            {commodities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code}
                {c.name && c.name !== c.code ? ` — ${c.name}` : ""}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="field-row">
        <Field label={t("Description")}>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label={t("Code")} hint={t("Account number or chart code")}>
          <input type="text" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label={t("Institution")}>
          <input type="text" value={institution} onChange={(e) => setInstitution(e.target.value)} />
        </Field>
        <Field label={t("IBAN")}>
          <input type="text" value={iban} onChange={(e) => setIban(e.target.value)} />
        </Field>
      </div>
      <Field label={t("Notes")}>
        <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <div className="toolbar__toggles">
        <Toggle
          label={t("Placeholder")}
          hint={t("Groups sub-accounts and takes no postings of its own.")}
          checked={placeholder}
          onChange={setPlaceholder}
        />
        {existing && (
          <Toggle label={t("Closed")} hint={t("A closed account keeps its history but rejects new postings.")} checked={closed} onChange={setClosed} />
        )}
      </div>
      {existing && parentId && byId.get(parentId)?.id !== existing.parent_id && (
        <p className="muted">{t("Moving keeps every posting; only the path changes.")}</p>
      )}
      {existing && mergeTargets.length > 0 && (
        <div className="account-editor__merge">
          <Field
            label={t("Merge into")}
            hint={t("Every posting and sub-account moves to the chosen account; this one disappears. Frozen periods are never rewritten.")}
          >
            <select
              value={mergeTarget}
              onChange={(e) => {
                setMergeTarget(e.target.value);
                setConfirmingMerge(false);
              }}
            >
              <option value="">{t("— choose an account —")}</option>
              {mergeTargets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.path}
                </option>
              ))}
            </select>
          </Field>
          {mergeTarget &&
            (confirmingMerge ? (
              <div className="actions actions--inline">
                <span className="muted">
                  {t("Merge {source} into {target}? There is no undo.", {
                    source: existing.path,
                    target: byId.get(mergeTarget)?.path ?? "?",
                  })}
                </span>
                <button type="button" className="btn btn--small btn--danger" disabled={change.isPending} onClick={() => void merge()}>
                  {t("Yes, merge")}
                </button>
                <button type="button" className="btn btn--small" onClick={() => setConfirmingMerge(false)}>
                  {t("No")}
                </button>
              </div>
            ) : (
              <button type="button" className="btn btn--danger" disabled={change.isPending} onClick={() => setConfirmingMerge(true)}>
                {t("Merge…")}
              </button>
            ))}
        </div>
      )}
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={change.isPending || !name.trim() || !!clash}>
          {change.isPending ? "…" : existing ? t("Save") : t("Create account")}
        </button>
        <button type="button" className="btn" onClick={onDone} disabled={change.isPending}>
          {t("Cancel")}
        </button>
      </div>
    </form>
  );
}

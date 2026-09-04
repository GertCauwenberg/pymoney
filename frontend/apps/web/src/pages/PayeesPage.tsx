import { useT } from "@pymoney/core/hooks";
import type { Account, Payee } from "@pymoney/core";
import { useAccounts, useChangePayee, usePayees } from "@pymoney/core/hooks";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const CAN_WRITE = new Set(["editor", "owner"]);

/**
 * Payees: who the money went to or came from. Editors and owners add, edit,
 * merge duplicates and delete the unused -- the server refuses to delete one a
 * transaction still names, and says so. Like the accounts page, the one being
 * edited is in the URL, so a link can open the editor.
 */
export function PayeesPage() {
  const t = useT();
  const { bookId, role } = useBookContext();
  const payees = usePayees(bookId);
  const accounts = useAccounts(bookId, { include_closed: true });
  const change = useChangePayee(bookId);
  const [search, setSearch] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);
  const canWrite = role !== null && CAN_WRITE.has(role);
  const editing = search.get("edit");
  const editingPayee = payees.data?.find((p) => p.id === editing);
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

  const accountsById = useMemo(() => new Map((accounts.data ?? []).map((a) => [a.id, a])), [accounts.data]);
  const rows = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return [...(payees.data ?? [])]
      .filter((p) => !needle || p.name.toLowerCase().includes(needle) || p.match_keys.some((k) => k.toLowerCase().includes(needle)))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [payees.data, filter]);

  const remove = (payee: Payee) => {
    setDeleteError(null);
    change.mutate(
      { kind: "delete", payeeId: payee.id },
      { onSuccess: () => setConfirming(null), onError: (err) => setDeleteError(err) },
    );
  };

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../../reports/balance-sheet">{t("Reports")}</Link> › {t("Settings")} › {t("Payees")}
          </p>
          <h1>{t("Payees")}</h1>
          <p className="muted">
            {canWrite
              ? t("Who the money went to or came from. Merge duplicates; a payee no transaction names can be deleted.")
              : t("Who the money went to or came from. Editors and owners can change them.")}
          </p>
        </div>
        <div className="actions">
          <input type="search" placeholder={t("Filter payees")} aria-label={t("Filter payees")} value={filter} onChange={(e) => setFilter(e.target.value)} />
          {canWrite && !creating && (
            <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
              {t("New payee")}
            </button>
          )}
        </div>
      </header>

      {payees.isPending && <Loading />}
      {payees.isError && <ErrorBanner error={payees.error} onRetry={() => void payees.refetch()} />}
      {deleteError !== null && <ErrorBanner error={deleteError} title={t("Could not delete")} />}

      {creating && payees.data && accounts.data && (
        <PayeeEditor bookId={bookId} payees={payees.data} accounts={accounts.data} onDone={() => setCreating(false)} />
      )}
      {canWrite && editingPayee && payees.data && accounts.data && (
        <PayeeEditor bookId={bookId} payees={payees.data} accounts={accounts.data} existing={editingPayee} onDone={() => setEditing(null)} />
      )}

      {payees.data && (
        <table className="report payees">
          <thead>
            <tr>
              <th scope="col">{t("Payee")}</th>
              <th scope="col">{t("Also matches")}</th>
              <th scope="col">{t("Default account")}</th>
              <th scope="col">{t("Notes")}</th>
              {canWrite && <th scope="col" className="num" />}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={canWrite ? 5 : 4} className="empty">
                  {payees.data.length === 0 ? t("No payees yet. One is created the first time you name one on a transaction.") : t("Nothing matches.")}
                </td>
              </tr>
            )}
            {rows.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link to={`../../search?payee=${p.id}`} className="account-link" title={t("Transactions with this payee")}>
                    {p.name}
                  </Link>
                </td>
                <td className="muted">{p.match_keys.join(", ")}</td>
                <td className="muted">{p.default_account_id ? accountsById.get(p.default_account_id)?.path ?? "?" : ""}</td>
                <td className="muted">{p.notes}</td>
                {canWrite && (
                  <td className="num">
                    <span className="actions actions--inline">
                      <button type="button" className="btn btn--small" onClick={() => setEditing(p.id)} aria-label={t("Edit {name}", { name: p.name })}>
                        {t("Edit")}
                      </button>
                      {confirming === p.id ? (
                        <>
                          <span className="muted">{t("Delete {name}?", { name: p.name })}</span>
                          <button type="button" className="btn btn--small btn--danger" disabled={change.isPending} onClick={() => remove(p)}>
                            {t("Yes, delete")}
                          </button>
                          <button type="button" className="btn btn--small" onClick={() => setConfirming(null)}>
                            {t("No")}
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--small btn--danger"
                          onClick={() => {
                            setDeleteError(null);
                            setConfirming(p.id);
                          }}
                          aria-label={t("Delete {name}", { name: p.name })}
                          title={t("Only a payee no transaction names can be deleted; merge it into another otherwise.")}
                        >
                          {t("Delete")}
                        </button>
                      )}
                    </span>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}

function PayeeEditor({
  bookId,
  payees,
  accounts,
  existing,
  onDone,
}: {
  bookId: string;
  payees: readonly Payee[];
  accounts: readonly Account[];
  existing?: Payee;
  onDone: () => void;
}) {
  const t = useT();
  const change = useChangePayee(bookId);
  const [name, setName] = useState(existing?.name ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [address, setAddress] = useState(existing?.address ?? "");
  const [matchKeys, setMatchKeys] = useState(existing?.match_keys.join(", ") ?? "");
  const [defaultAccount, setDefaultAccount] = useState(existing?.default_account_id ?? "");
  const [mergeTarget, setMergeTarget] = useState("");
  const [confirmingMerge, setConfirmingMerge] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!existing) return;
    setName(existing.name);
    setNotes(existing.notes);
    setAddress(existing.address);
    setMatchKeys(existing.match_keys.join(", "));
    setDefaultAccount(existing.default_account_id ?? "");
    setMergeTarget("");
    setConfirmingMerge(false);
    setError(null);
  }, [existing]);

  const keys = matchKeys
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  const clash = payees.find((p) => p.id !== existing?.id && p.name.trim().toLowerCase() === name.trim().toLowerCase());
  const postable = accounts.filter((a) => !a.placeholder && !a.closed).sort((a, b) => a.path.localeCompare(b.path));
  const mergeTargets = payees.filter((p) => p.id !== existing?.id).sort((a, b) => a.name.localeCompare(b.name));
  const byId = new Map(payees.map((p) => [p.id, p]));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      if (!existing) {
        await change.mutateAsync({
          kind: "create",
          body: { name: name.trim(), notes: notes.trim(), address: address.trim(), match_keys: keys, default_account_id: defaultAccount || null },
        });
      } else {
        const patch: Record<string, unknown> = {};
        if (name.trim() !== existing.name) patch["name"] = name.trim();
        if (notes.trim() !== existing.notes) patch["notes"] = notes.trim();
        if (address.trim() !== existing.address) patch["address"] = address.trim();
        if (keys.join("\u0000") !== existing.match_keys.join("\u0000")) patch["match_keys"] = keys;
        if ((defaultAccount || null) !== (existing.default_account_id ?? null)) patch["default_account_id"] = defaultAccount || null;
        if (Object.keys(patch).length > 0) await change.mutateAsync({ kind: "patch", payeeId: existing.id, body: patch });
      }
      onDone();
    } catch (err) {
      setError(err);
    }
  };

  const merge = async () => {
    if (!existing || !mergeTarget) return;
    setError(null);
    try {
      await change.mutateAsync({ kind: "merge", targetId: mergeTarget, sourceIds: [existing.id] });
      onDone();
    } catch (err) {
      setConfirmingMerge(false);
      setError(err);
    }
  };

  return (
    <form className="card account-editor" onSubmit={(e) => void submit(e)} aria-label={existing ? `Edit ${existing.name}` : t("New payee")}>
      <h2>{existing ? `${t("Edit")} ${existing.name}` : t("New payee")}</h2>
      {error !== null && <ErrorBanner error={error} title={t("Could not save")} />}
      <div className="field-row">
        <Field label={t("Name")} hint={clash ? t("{name} already exists.", { name: clash.name }) : undefined}>
          <input type="text" required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t("Default account")} hint={t("Pre-filled when you post to this payee.")}>
          <select value={defaultAccount} onChange={(e) => setDefaultAccount(e.target.value)}>
            <option value="">—</option>
            {postable.map((a) => (
              <option key={a.id} value={a.id}>
                {a.path}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="field-row">
        <Field label={t("Also matches")} hint={t("Other spellings, comma-separated; an import uses them to find this payee.")}>
          <input type="text" value={matchKeys} onChange={(e) => setMatchKeys(e.target.value)} />
        </Field>
        <Field label={t("Address")}>
          <input type="text" value={address} onChange={(e) => setAddress(e.target.value)} />
        </Field>
      </div>
      <Field label={t("Notes")}>
        <input type="text" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      {existing && mergeTargets.length > 0 && (
        <div className="account-editor__merge">
          <Field label={t("Merge into")} hint={t("Every transaction naming this payee is moved to the chosen one; this one disappears. Its spellings are kept as matches.")}>
            <select
              value={mergeTarget}
              onChange={(e) => {
                setMergeTarget(e.target.value);
                setConfirmingMerge(false);
              }}
            >
              <option value="">{t("— choose a payee —")}</option>
              {mergeTargets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          {mergeTarget &&
            (confirmingMerge ? (
              <div className="actions actions--inline">
                <span className="muted">
                  {t("Merge {source} into {target}? There is no undo.", { source: existing.name, target: byId.get(mergeTarget)?.name ?? "?" })}
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
          {change.isPending ? "…" : existing ? t("Save") : t("Create payee")}
        </button>
        <button type="button" className="btn" onClick={onDone} disabled={change.isPending}>
          {t("Cancel")}
        </button>
      </div>
    </form>
  );
}

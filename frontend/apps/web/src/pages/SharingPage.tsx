import { useT } from "@pymoney/core/hooks";
import type { Grant, Role } from "@pymoney/core";
import { useBook, useDeleteBook, useDeleteGrant, useGrants, usePutGrant, useSession } from "@pymoney/core/hooks";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const ROLES: { id: Role; label: string; blurb: string }[] = [
  { id: "viewer", label: "Viewer", blurb: "can see everything, change nothing" },
  { id: "editor", label: "Editor", blurb: "can also post and edit transactions, and add payees" },
  { id: "owner", label: "Owner", blurb: "can also manage the accounts, rename the book and manage sharing" },
];

/**
 * Who can see this book. Owners only: the server answers 403 to anyone else,
 * and the tab is not shown to them.
 *
 * Grants are keyed on the other person's email, which they must register
 * with -- the server does not check that the account exists yet, so a typo
 * simply grants access to nobody. The page says so.
 */
export function SharingPage() {
  const t = useT();
  const { bookId, role } = useBookContext();
  const me = useSession();
  const grants = useGrants(bookId, role === "owner");
  const put = usePutGrant(bookId);
  const del = useDeleteGrant(bookId);
  const [subject, setSubject] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const book = useBook(bookId);

  if (role !== "owner") return <Navigate to="../reports/balance-sheet" replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    put.mutate({ subject: subject.trim().toLowerCase(), role: newRole }, { onSuccess: () => setSubject("") });
  };

  const owners = grants.data?.filter((g) => g.role === "owner").length ?? 0;
  const myself = me.data?.email;

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../reports/balance-sheet">{t("Reports")}</Link> › Sharing
          </p>
          <h1>{t("Sharing")}</h1>
          <p className="muted">{t("Who can see this book, and what they may do.")}</p>
        </div>
      </header>

      {grants.isPending && <Loading />}
      {grants.isError && <ErrorBanner error={grants.error} onRetry={() => void grants.refetch()} />}
      {del.error && <ErrorBanner error={del.error} title={t("Could not revoke access")} />}

      {grants.data && (
        <table className="report grants">
          <thead>
            <tr>
              <th scope="col">{t("Person")}</th>
              <th scope="col">{t("Role")}</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {grants.data.map((g) => (
              <GrantRow
                key={g.subject}
                grant={g}
                isMe={g.subject === myself}
                lastOwner={g.role === "owner" && owners === 1}
                busy={put.isPending || del.isPending}
                onRole={(r) => put.mutate({ subject: g.subject, role: r })}
                onRevoke={() => del.mutate(g.subject)}
              />
            ))}
          </tbody>
        </table>
      )}

      <form className="card sharing__form" onSubmit={submit}>
        <h2>{t("Share with someone")}</h2>
        <p className="muted">
          {t("Enter the email address they log in with (or will register with). Access is tied to that exact address, so check the spelling: a typo grants access to nobody.")}
        </p>
        {put.error && <ErrorBanner error={put.error} title={t("Could not share")} />}
        <div className="field-row">
          <Field label={t("Email")}>
            <input type="email" required value={subject} onChange={(e) => setSubject(e.target.value)} />
          </Field>
          <Field label={t("Role")} hint={t(ROLES.find((r) => r.id === newRole)?.blurb ?? "")}>
            <select value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r.id} value={r.id}>
                  {t(r.label)}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className="btn btn--primary" disabled={put.isPending || !subject.trim()}>
            {put.isPending ? "…" : t("Share")}
          </button>
        </div>
      </form>

      {book.data && <DeleteBook bookId={bookId} name={book.data.name} />}
    </article>
  );
}

/**
 * The one irreversible thing on this page. The server wants the book's exact
 * name back, so the user types it; the button stays disabled until it matches.
 */
function DeleteBook({ bookId, name }: { bookId: string; name: string }) {
  const t = useT();
  const del = useDeleteBook();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === name;
  return (
    <section className="card danger" aria-labelledby="danger-heading">
      <h2 id="danger-heading">{t("Delete this book")}</h2>
      <p className="muted">
        {t("Everything goes: every account, transaction, price and the whole history, for everyone it is shared with. There is no undo and no backup on the server. If you only want out, revoke your own access instead.")}
      </p>
      {!open ? (
        <button type="button" className="btn btn--danger" onClick={() => setOpen(true)}>
          {t("Delete this book…")}
        </button>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (matches) del.mutate({ bookId, name: typed.trim() }, { onSuccess: () => void navigate("/books", { state: { pick: true } }) });
          }}
        >
          {del.error && <ErrorBanner error={del.error} title={t("Could not delete the book")} />}
          <Field label={`Type the book's name (${name}) to confirm`}>
            <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </Field>
          <div className="actions">
            <button type="submit" className="btn btn--danger" disabled={!matches || del.isPending}>
              {del.isPending ? "…" : t("Delete it, irreversibly")}
            </button>
            <button type="button" className="btn" onClick={() => setOpen(false)} disabled={del.isPending}>
              {t("Keep it")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function GrantRow({
  grant,
  isMe,
  lastOwner,
  busy,
  onRole,
  onRevoke,
}: {
  grant: Grant;
  isMe: boolean;
  lastOwner: boolean;
  busy: boolean;
  onRole: (role: Role) => void;
  onRevoke: () => void;
}) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  return (
    <tr>
      <td>
        {grant.subject}
        {isMe && <span className="muted"> (you)</span>}
      </td>
      <td>
        <select
          aria-label={`Role of ${grant.subject}`}
          value={grant.role}
          disabled={busy || lastOwner}
          title={lastOwner ? "The last owner cannot be demoted; make someone else an owner first." : undefined}
          onChange={(e) => onRole(e.target.value as Role)}
        >
          {ROLES.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
      </td>
      <td className="num">
        {confirming ? (
          <span className="actions actions--inline">
            <span className="muted">{isMe ? t("Remove yourself? You will lose access.") : t("Revoke access?")}</span>
            <button type="button" className="btn btn--small btn--danger" disabled={busy} onClick={onRevoke}>
              {t("Yes, revoke")}
            </button>
            <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
              {t("No")}
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn btn--small"
            disabled={busy || lastOwner}
            title={lastOwner ? "The last owner cannot be removed; make someone else an owner first." : undefined}
            onClick={() => setConfirming(true)}
          >
            {t("Revoke")}
          </button>
        )}
      </td>
    </tr>
  );
}

import type { Grant, Role } from "@pymoney/core";
import { useDeleteGrant, useGrants, usePutGrant, useSession } from "@pymoney/core/hooks";
import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const ROLES: { id: Role; label: string; blurb: string }[] = [
  { id: "viewer", label: "Viewer", blurb: "can see everything, change nothing" },
  { id: "editor", label: "Editor", blurb: "can also post and edit transactions" },
  { id: "owner", label: "Owner", blurb: "can also rename the book and manage sharing" },
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
  const { bookId, role } = useBookContext();
  const me = useSession();
  const grants = useGrants(bookId, role === "owner");
  const put = usePutGrant(bookId);
  const del = useDeleteGrant(bookId);
  const [subject, setSubject] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");

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
            <Link to="../reports/balance-sheet">Reports</Link> › Sharing
          </p>
          <h1>Sharing</h1>
          <p className="muted">Who can see this book, and what they may do.</p>
        </div>
      </header>

      {grants.isPending && <Loading />}
      {grants.isError && <ErrorBanner error={grants.error} onRetry={() => void grants.refetch()} />}
      {del.error && <ErrorBanner error={del.error} title="Could not revoke access" />}

      {grants.data && (
        <table className="report grants">
          <thead>
            <tr>
              <th scope="col">Person</th>
              <th scope="col">Role</th>
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
        <h2>Share with someone</h2>
        <p className="muted">
          Enter the email address they log in with (or will register with). Access is tied to that exact
          address, so check the spelling: a typo grants access to nobody.
        </p>
        {put.error && <ErrorBanner error={put.error} title="Could not share" />}
        <div className="field-row">
          <Field label="Email">
            <input type="email" required value={subject} onChange={(e) => setSubject(e.target.value)} />
          </Field>
          <Field label="Role" hint={ROLES.find((r) => r.id === newRole)?.blurb}>
            <select value={newRole} onChange={(e) => setNewRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="actions">
          <button type="submit" className="btn btn--primary" disabled={put.isPending || !subject.trim()}>
            {put.isPending ? "…" : "Share"}
          </button>
        </div>
      </form>
    </article>
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
            <span className="muted">{isMe ? "Remove yourself? You will lose access." : "Revoke access?"}</span>
            <button type="button" className="btn btn--small btn--danger" disabled={busy} onClick={onRevoke}>
              Yes, revoke
            </button>
            <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
              No
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
            Revoke
          </button>
        )}
      </td>
    </tr>
  );
}

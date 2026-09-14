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
 */
export function DeletingPage() {
  const t = useT();
  const { bookId, role } = useBookContext();
  const me = useSession();
  const grants = useGrants(bookId, role === "owner");
  const put = usePutGrant(bookId);
  const del = useDeleteGrant(bookId);
  const [subject, setSubject] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const book = useBook(bookId);

  if (role !== "owner") return <Navigate to="../../reports/balance-sheet" replace />;

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
            <Link to="../../reports/balance-sheet">{t("Reports")}</Link> › {t("Delete")}
          </p>
          <h1>{t("Delete")}</h1>
          <p className="muted">{t("Delete this book")}</p>
        </div>
      </header>

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

import { useLocale, useT } from "@pymoney/core/hooks";
import type { Account, Change } from "@pymoney/core";
import { Link } from "react-router-dom";

/** What a change log entry means to a person, not to the sync protocol.
 * Kept as label tables so scripts/i18n.py collects them for the catalogue. */
const KINDS: readonly { id: string; label: string }[] = [
  { id: "TRANSACTION", label: "transaction" },
  { id: "ACCOUNT", label: "account" },
  { id: "PAYEE", label: "payee" },
  { id: "COMMODITY", label: "commodity" },
  { id: "PRICE", label: "price" },
  { id: "BOOK", label: "book" },
];
const OPS: readonly { id: string; label: string }[] = [
  { id: "CREATE", label: "created" },
  { id: "UPDATE", label: "changed" },
  { id: "DELETE", label: "deleted" },
];
export const KIND_LABELS: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.id, k.label]));
const OP_LABELS: Record<string, string> = Object.fromEntries(OPS.map((o) => [o.id, o.label]));

export function HistoryTable({ changes, accounts, linkTransactions = true }: { changes: readonly Change[]; accounts?: ReadonlyMap<string, Account>; linkTransactions?: boolean }) {
  const t = useT();
  const locale = useLocale();
  if (changes.length === 0) return <p className="empty">{t("No changes recorded.")}</p>;
  const when = (iso: string) => new Date(iso).toLocaleString(locale === "nl" ? "nl-NL" : "en-GB");
  return (
    <table className="report history">
      <thead>
        <tr>
          <th scope="col">{t("When")}</th>
          <th scope="col">{t("Who")}</th>
          <th scope="col">{t("What")}</th>
          <th scope="col" className="num">
            {t("Revision")}
          </th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => {
          const kind = t(KIND_LABELS[c.kind] ?? c.kind.toLowerCase());
          const op = t(OP_LABELS[c.op] ?? c.op.toLowerCase());
          const name = c.kind === "ACCOUNT" ? accounts?.get(c.entity_id)?.path : undefined;
          return (
            <tr key={`${c.revision}-${c.entity_id}-${i}`}>
              <td className="register__date">{when(c.at)}</td>
              <td>{c.actor || <span className="muted">—</span>}</td>
              <td>
                {kind} {op}
                {name && <> · {name}</>}
                {c.kind === "TRANSACTION" && linkTransactions && c.op !== "DELETE" && (
                  <>
                    {" "}
                    · <Link to={`../../transactions/${c.entity_id}`}>{t("open")}</Link>
                  </>
                )}
                {c.note && <span className="muted"> — {c.note}</span>}
                <span className="muted history__command"> ({c.command})</span>
              </td>
              <td className="num muted">{c.revision}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

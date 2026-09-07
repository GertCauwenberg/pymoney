/**
 * "3 scheduled transactions are due." The banner on every book page.
 *
 * It never posts on its own. Each occurrence is posted on the user's click
 * (auto_post schedules offer a plain one-click; the rest the same, since this
 * *is* the confirmation), through the ordinary idempotent endpoint.
 */
import { useT } from "@pymoney/core/hooks";
import type { DueSchedule } from "@pymoney/core";
import { useDueSchedules, usePostOccurrence, useSkipOccurrence } from "@pymoney/core/hooks";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Money } from "./Money";

const CAN_WRITE = new Set(["editor", "owner"]);

export function DuePanel({ bookId, role }: { bookId: string; role: string | null }) {
  const t = useT();
  const due = useDueSchedules(bookId);
  const post = usePostOccurrence(bookId);
  const skip = useSkipOccurrence(bookId);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const canWrite = role !== null && CAN_WRITE.has(role);
  if (!due.data || due.data.occurrences === 0) return null;
  const rows = due.data.due;

  const act = async (fn: () => Promise<unknown>, key: string) => {
    setBusy(key);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="due-panel" aria-label={t("Scheduled transactions due")}>
      <button type="button" className="due-panel__summary" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <strong>
          {t("{n} scheduled transaction(s) due", { n: due.data.occurrences })}
        </strong>
        <span>{open ? t("Hide") : t("Review")}</span>
      </button>
      {open && (
        <ul className="due-panel__list">
          {rows.map((row) => (
            <DueRow
              key={row.schedule.id}
              row={row}
              canWrite={canWrite}
              busy={busy?.startsWith(row.schedule.id) ?? false}
              onPost={() =>
                act(
                  () => post.mutateAsync({ scheduleId: row.schedule.id, idempotencyKey: `${row.schedule.id}:${row.pending[0]}` }),
                  `${row.schedule.id}:post`,
                )
              }
              onSkip={() => act(() => skip.mutateAsync(row.pending[0] ? { scheduleId: row.schedule.id, on: row.pending[0] } : { scheduleId: row.schedule.id }), `${row.schedule.id}:skip`)}
            />
          ))}
          <li className="due-panel__foot">
            <Link to={`/books/${bookId}/settings/schedules`}>{t("Manage schedules")}</Link>
          </li>
        </ul>
      )}
    </section>
  );
}

function DueRow({
  row,
  canWrite,
  busy,
  onPost,
  onSkip,
}: {
  row: DueSchedule;
  canWrite: boolean;
  busy: boolean;
  onPost: () => void;
  onSkip: () => void;
}) {
  const t = useT();
  const extra = row.pending.length - 1;
  return (
    <li className="due-panel__row">
      <div>
        <strong>{row.schedule.name}</strong>
        <span className="muted">
          {" "}
          {row.pending[0]}
          {extra > 0 && ` ${t("(+{n} earlier)", { n: extra })}`}
          {row.schedule.template.estimate && ` · ${t("estimate")}`}
        </span>
      </div>
      {row.amount ? <Money amount={row.amount} withCommodity /> : <span className="muted">—</span>}
      {canWrite && (
        <div className="due-panel__actions">
          <button type="button" className="btn btn--small btn--primary" disabled={busy} onClick={onPost}>
            {t("Post")}
          </button>
          <button type="button" className="btn btn--small" disabled={busy} onClick={onSkip}>
            {t("Skip")}
          </button>
        </div>
      )}
    </li>
  );
}

/**
 * Scheduled / recurring transactions: rent, salary, subscriptions.
 *
 * Nothing posts on a timer. This page manages the schedules; the
 * {@link DuePanel} that shows on the book pages is what posts an occurrence,
 * on confirmation. A schedule here is a simple two-sided template (money out
 * of one account into a category, or the reverse for income) plus a
 * recurrence rule -- enough for the everyday cases.
 */
import { useT } from "@pymoney/core/hooks";
import type { Account, Frequency, Schedule, ScheduleIn } from "@pymoney/core";
import { useAccounts, useChangeSchedule, useSchedules } from "@pymoney/core/hooks";
import { useMemo, useState, type FormEvent } from "react";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field, Toggle } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const CAN_WRITE = new Set(["editor", "owner"]);
const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "DAILY", label: "day" },
  { value: "WEEKLY", label: "week" },
  { value: "MONTHLY", label: "month" },
  { value: "YEARLY", label: "year" },
];

export function SchedulesPage() {
  const t = useT();
  const { bookId, role } = useBookContext();
  const canWrite = role !== null && CAN_WRITE.has(role);
  const schedules = useSchedules(bookId);
  const accounts = useAccounts(bookId, { include_closed: false });
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);

  if (schedules.isError) return <ErrorBanner error={schedules.error} onRetry={() => void schedules.refetch()} />;
  if (!schedules.data || !accounts.data) return <Loading label={t("Loading schedules…")} />;

  const postable = accounts.data.filter((a) => !a.placeholder).sort((a, b) => a.path.localeCompare(b.path));

  return (
    <article className="report-page schedules">
      <div className="page-heading">
        <h2>{t("Scheduled transactions")}</h2>
        {canWrite && !editing && (
          <button type="button" className="btn" onClick={() => setEditing("new")}>
            {t("New schedule")}
          </button>
        )}
      </div>
      <p className="muted">
        {t("Rent, a salary, a subscription. Nothing is posted automatically — when one comes due you confirm it from the banner at the top of the book.")}
      </p>

      {editing && (
        <ScheduleForm
          bookId={bookId}
          accounts={postable}
          existing={editing === "new" ? null : editing}
          onDone={() => setEditing(null)}
        />
      )}

      {schedules.data.length === 0 && !editing ? (
        <p className="muted">{t("No schedules yet.")}</p>
      ) : (
        <table className="schedules__table">
          <thead>
            <tr>
              <th>{t("Name")}</th>
              <th>{t("Repeats")}</th>
              <th>{t("Next")}</th>
              <th>{t("Upcoming")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {schedules.data.map((s) => (
              <tr key={s.id} className={s.paused ? "is-paused" : s.finished ? "is-finished" : ""}>
                <td>
                  {s.name}
                  {s.template.estimate && <span className="muted"> ≈</span>}
                  {s.paused && <span className="tag tag--muted">{t("paused")}</span>}
                  {s.finished && <span className="tag tag--muted">{t("finished")}</span>}
                </td>
                <td>{s.recurrence_text}</td>
                <td>{s.next_due ?? "—"}</td>
                <td className="muted">{(s.upcoming ?? []).slice(1, 4).join(", ")}</td>
                <td className="num">
                  {canWrite && (
                    <button type="button" className="btn btn--link" onClick={() => setEditing(s)}>
                      {t("Edit")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}

function ScheduleForm({
  bookId,
  accounts,
  existing,
  onDone,
}: {
  bookId: string;
  accounts: readonly Account[];
  existing: Schedule | null;
  onDone: () => void;
}) {
  const t = useT();
  const change = useChangeSchedule(bookId);
  const fromLeg = existing?.template.legs.find((l) => !l.is_residual) ?? null;
  const toLeg = existing?.template.legs.find((l) => l.is_residual) ?? null;

  const today = new Date().toISOString().slice(0, 10);
  const [name, setName] = useState(existing?.name ?? "");
  const [from, setFrom] = useState(fromLeg?.account_id ?? accounts[0]?.id ?? "");
  const [to, setTo] = useState(toLeg?.account_id ?? accounts[1]?.id ?? "");
  const [amount, setAmount] = useState(
    fromLeg?.value ? fromLeg.value.replace(/^-/, "") : "",
  );
  const [incoming, setIncoming] = useState((fromLeg?.value ?? "-1").startsWith("-") === false);
  const [memo, setMemo] = useState(existing?.template.memo ?? "");
  const [estimate, setEstimate] = useState(existing?.template.estimate ?? false);
  const [autoPost, setAutoPost] = useState(existing?.auto_post ?? false);
  const [paused, setPaused] = useState(existing?.paused ?? false);

  const [frequency, setFrequency] = useState<Frequency>(existing?.recurrence.frequency ?? "MONTHLY");
  const [interval, setInterval] = useState(existing?.recurrence.interval ?? 1);
  const [start, setStart] = useState(existing?.recurrence.start ?? today);
  const [byWeekday, setByWeekday] = useState(existing?.recurrence.monthly_by_weekday ?? false);
  const [endMode, setEndMode] = useState<"never" | "count" | "until">(
    existing?.recurrence.count != null ? "count" : existing?.recurrence.until != null ? "until" : "never",
  );
  const [count, setCount] = useState(existing?.recurrence.count ?? 12);
  const [until, setUntil] = useState(existing?.recurrence.until ?? "");

  const signed = useMemo(() => (amount.trim() ? `${incoming ? "" : "-"}${amount.trim()}` : ""), [amount, incoming]);
  const problem =
    !name.trim() ? t("Give the schedule a name.")
    : !from || !to || from === to ? t("Choose two different accounts.")
    : !signed || signed === "-" ? t("Enter an amount.")
    : endMode === "until" && !until ? t("Choose an end date.")
    : null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (problem) return;
    const body: ScheduleIn = {
      name: name.trim(),
      auto_post: autoPost,
      paused,
      recurrence: {
        frequency,
        interval: Math.max(1, interval),
        start,
        monthly_by_weekday: frequency === "MONTHLY" ? byWeekday : false,
        count: endMode === "count" ? Math.max(1, count) : null,
        until: endMode === "until" ? until : null,
      },
      template: {
        legs: [
          { account_id: from, value: signed, memo: "" },
          { account_id: to, memo: "" },
        ],
        memo: memo.trim(),
        number: "",
        rounding: "HALF_EVEN",
        estimate,
      },
    };
    change.mutate(
      existing ? { kind: "update", scheduleId: existing.id, body } : { kind: "create", body },
      { onSuccess: onDone },
    );
  };

  return (
    <form className="card" onSubmit={submit} aria-label={existing ? t("Edit schedule") : t("New schedule")}>
      {change.error && <ErrorBanner error={change.error} title={t("Could not save the schedule")} />}
      <Field label={t("Name")}>
        <input value={name} maxLength={255} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>

      <fieldset className="schedules__legs">
        <legend>{t("Each time, post")}</legend>
        <Toggle label={t("Money coming in (a salary)")} checked={incoming} onChange={setIncoming} />
        <div className="schedules__leg-row">
          <Field label={incoming ? t("Into") : t("Out of")}>
            <select value={from} onChange={(e) => setFrom(e.target.value)}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.path}</option>
              ))}
            </select>
          </Field>
          <Field label={t("Amount")} hint={t('Exact; "12.34" or "10/3".')}>
            <input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
          </Field>
          <Field label={incoming ? t("From category") : t("Category")}>
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.path}</option>
              ))}
            </select>
          </Field>
        </div>
        <Field label={t("Memo")}>
          <input value={memo} maxLength={500} onChange={(e) => setMemo(e.target.value)} />
        </Field>
        <Toggle
          label={t("The amount is an estimate")}
          hint={t("Shown with ≈; you confirm the real figure when it is due.")}
          checked={estimate}
          onChange={setEstimate}
        />
      </fieldset>

      <fieldset className="schedules__rule">
        <legend>{t("Repeats")}</legend>
        <div className="schedules__leg-row">
          <Field label={t("Every")}>
            <input
              type="number"
              min={1}
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value) || 1)}
            />
          </Field>
          <Field label={t("Unit")}>
            <select value={frequency} onChange={(e) => setFrequency(e.target.value as Frequency)}>
              {FREQUENCIES.map((f) => (
                <option key={f.value} value={f.value}>{t(f.label)}</option>
              ))}
            </select>
          </Field>
          <Field label={t("Starting")}>
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
        </div>
        {frequency === "MONTHLY" && (
          <Toggle
            label={t("On the same weekday (a 'third Friday') rather than the date")}
            checked={byWeekday}
            onChange={setByWeekday}
          />
        )}
        <div className="schedules__leg-row">
          <Field label={t("Ends")}>
            <select value={endMode} onChange={(e) => setEndMode(e.target.value as typeof endMode)}>
              <option value="never">{t("never")}</option>
              <option value="count">{t("after N times")}</option>
              <option value="until">{t("on a date")}</option>
            </select>
          </Field>
          {endMode === "count" && (
            <Field label={t("Times")}>
              <input type="number" min={1} value={count} onChange={(e) => setCount(Number(e.target.value) || 1)} />
            </Field>
          )}
          {endMode === "until" && (
            <Field label={t("End date")}>
              <input type="date" value={until} min={start} onChange={(e) => setUntil(e.target.value)} />
            </Field>
          )}
        </div>
      </fieldset>

      <div className="toolbar__toggles">
        <Toggle
          label={t("Post without asking")}
          hint={t("A due occurrence is posted the next time the book is open, with no confirmation.")}
          checked={autoPost}
          onChange={setAutoPost}
        />
        <Toggle label={t("Paused")} checked={paused} onChange={setPaused} />
      </div>

      {problem && <p className="field__hint" role="alert">{problem}</p>}
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={change.isPending || !!problem}>
          {change.isPending ? "…" : existing ? t("Save") : t("Create schedule")}
        </button>
        <button type="button" className="btn btn--link" onClick={onDone} disabled={change.isPending}>
          {t("Cancel")}
        </button>
        {existing && (
          <button
            type="button"
            className="btn btn--danger"
            disabled={change.isPending}
            onClick={() => change.mutate({ kind: "delete", scheduleId: existing.id }, { onSuccess: onDone })}
          >
            {t("Delete")}
          </button>
        )}
      </div>
    </form>
  );
}

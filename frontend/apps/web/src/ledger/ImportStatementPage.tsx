/**
 * Import a bank statement (CSV) into one account.
 *
 * Four steps, one per screen: choose the file, confirm the column mapping,
 * review what each row would become, see what was posted. The mapping is
 * remembered on the server, so the second week's download opens straight on
 * the review screen.
 */
import { useT } from "@pymoney/core/hooks";
import type {
  Account,
  ColumnMapping,
  CsvInspect,
  CsvPreview,
  ImportDecision,
  ImportProposal,
  Payee,
  StatementImport,
} from "@pymoney/core";
import {
  useAccount,
  useAccounts,
  useCommitCsv,
  useImportMapping,
  useInspectCsv,
  usePayees,
  usePreviewCsv,
  useSaveImportMapping,
} from "@pymoney/core/hooks";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Money } from "../components/Money";
import { Field, Toggle } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const CAN_WRITE = new Set(["editor", "owner"]);

type Step =
  | { name: "upload" }
  | { name: "map"; file: File; inspect: CsvInspect; mapping: ColumnMapping }
  | { name: "review"; file: File; inspect: CsvInspect; mapping: ColumnMapping; preview: CsvPreview }
  | { name: "done"; report: StatementImport };

export function ImportStatementPage() {
  const t = useT();
  const { bookId, role } = useBookContext();
  const { accountId = "" } = useParams();
  const account = useAccount(bookId, accountId);
  const accounts = useAccounts(bookId, { include_closed: false });
  const payees = usePayees(bookId);
  const [step, setStep] = useState<Step>({ name: "upload" });

  const here = `/books/${bookId}/accounts/${accountId}`;
  const canWrite = role !== null && CAN_WRITE.has(role);

  if (account.isError) {
    return (
      <article className="report-page">
        <ErrorBanner error={account.error} onRetry={() => void account.refetch()} />
      </article>
    );
  }
  if (!account.data || !accounts.data || !payees.data) return <Loading label={t("Loading…")} />;

  if (!canWrite || account.data.placeholder) {
    return (
      <article className="report-page">
        <h1>{t("Import a statement")}</h1>
        <ErrorBanner
          error={new Error(
            account.data.placeholder
              ? t("This is a placeholder account; import into one of its sub-accounts.")
              : t("Importing needs editor access to the book."),
          )}
        />
        <p>
          <Link to={here}>{t("Back to the register")}</Link>
        </p>
      </article>
    );
  }

  return (
    <article className="report-page import-statement">
      <header className="report-page__header">
        <div>
          <p className="muted">
            <Link to={here}>{account.data.name}</Link>
          </p>
          <h1>{t("Import a statement")}</h1>
        </div>
      </header>

      <ol className="import-steps" aria-label={t("Import steps")}>
        {(["upload", "map", "review", "done"] as const).map((name, i) => (
          <li key={name} aria-current={step.name === name ? "step" : undefined} className={stepClass(step.name, name)}>
            <span className="import-steps__n">{i + 1}</span>
            {{ upload: t("File"), map: t("Columns"), review: t("Review"), done: t("Done") }[name]}
          </li>
        ))}
      </ol>

      {step.name === "upload" && (
        <UploadStep
          bookId={bookId}
          accountId={accountId}
          onReady={(file, inspect, mapping) => setStep({ name: "map", file, inspect, mapping })}
        />
      )}
      {step.name === "map" && (
        <MappingStep
          bookId={bookId}
          accountId={accountId}
          file={step.file}
          inspect={step.inspect}
          mapping={step.mapping}
          onBack={() => setStep({ name: "upload" })}
          onPreviewed={(mapping, preview) =>
            setStep({ name: "review", file: step.file, inspect: step.inspect, mapping, preview })
          }
        />
      )}
      {step.name === "review" && (
        <ReviewStep
          bookId={bookId}
          accountId={accountId}
          file={step.file}
          mapping={step.mapping}
          preview={step.preview}
          accounts={accounts.data}
          payees={payees.data}
          onBack={() =>
            setStep({ name: "map", file: step.file, inspect: step.inspect, mapping: step.mapping })
          }
          onImported={(report) => setStep({ name: "done", report })}
        />
      )}
      {step.name === "done" && (
        <ReportStep
          report={step.report}
          registerHref={here}
          onAnother={() => setStep({ name: "upload" })}
        />
      )}
    </article>
  );
}

function stepClass(current: Step["name"], name: Step["name"]): string {
  const order = ["upload", "map", "review", "done"];
  if (current === name) return "is-current";
  return order.indexOf(current) > order.indexOf(name) ? "is-done" : "is-todo";
}

// ===========================================================================
// 1. the file
// ===========================================================================
function UploadStep({
  bookId,
  accountId,
  onReady,
}: {
  bookId: string;
  accountId: string;
  onReady: (file: File, inspect: CsvInspect, mapping: ColumnMapping) => void;
}) {
  const t = useT();
  const inspect = useInspectCsv(bookId);
  const saved = useImportMapping(bookId, accountId);

  const pick = (file: File | null) => {
    if (!file) return;
    inspect.mutate(file, {
      onSuccess: (result) => {
        // The saved mapping wins when it still fits the file's columns; it is
        // the one the household confirmed last time.
        const columns = new Set(result.columns);
        const remembered = saved.data && both(saved.data, columns) ? saved.data : null;
        onReady(file, result, remembered ?? result.suggested_mapping);
      },
    });
  };

  return (
    <section className="card">
      <p className="muted">
        {t(
          "Upload a CSV your bank exported. PyMoney reads the columns, matches each row to a payee and a category, and shows you everything before a single transaction is posted.",
        )}
      </p>
      {inspect.error && <ErrorBanner error={inspect.error} title={t("Could not read the file")} />}
      <Field label={t("Statement file")}>
        <input
          type="file"
          accept=".csv,text/csv,text/plain,application/vnd.ms-excel"
          disabled={inspect.isPending}
          onChange={(e) => pick(e.target.files?.[0] ?? null)}
        />
      </Field>
      {inspect.isPending && <p className="muted">{t("Reading the file…")}</p>}
    </section>
  );
}

/** True if every column the mapping relies on is present in the file. */
function both(mapping: ColumnMapping, columns: Set<string>): boolean {
  const used = [
    mapping.date,
    mapping.amount,
    mapping.debit,
    mapping.credit,
    mapping.reference,
    mapping.memo,
    ...(mapping.description ?? []),
  ].filter(Boolean);
  return used.every((name) => columns.has(name));
}

// ===========================================================================
// 2. the column mapping
// ===========================================================================
const NONE = "\u0000none";

function MappingStep({
  bookId,
  accountId,
  file,
  inspect,
  mapping,
  onBack,
  onPreviewed,
}: {
  bookId: string;
  accountId: string;
  file: File;
  inspect: CsvInspect;
  mapping: ColumnMapping;
  onBack: () => void;
  onPreviewed: (mapping: ColumnMapping, preview: CsvPreview) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState<ColumnMapping>(mapping);
  const preview = usePreviewCsv(bookId, accountId);
  const columns = inspect.columns.length ? inspect.columns : namesFrom(inspect.sample_rows);

  const set = (patch: Partial<ColumnMapping>) => setDraft((d) => ({ ...d, ...patch }));
  const pick = (value: string) => (value === NONE ? "" : value);

  const columnSelect = (value: string, onChange: (v: string) => void, withNone = true) => (
    <select value={value || NONE} onChange={(e) => onChange(pick(e.target.value))}>
      {withNone && <option value={NONE}>{t("— none —")}</option>}
      {columns.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </select>
  );

  const go = () => preview.mutate({ file, mapping: draft }, { onSuccess: (p) => onPreviewed(draft, p) });

  return (
    <section className="card import-mapping">
      <p className="muted">
        {t("Tell PyMoney which column is which. It has guessed; correct anything it got wrong.")}
      </p>
      {preview.error && <ErrorBanner error={preview.error} title={t("That mapping does not fit the file")} />}

      <div className="import-mapping__grid">
        <Field label={t("Date")}>{columnSelect(draft.date, (date) => set({ date }), false)}</Field>
        <Field label={t("Date format")} hint={t("Leave blank to detect it; or e.g. %d/%m/%Y.")}>
          <input value={draft.date_format} onChange={(e) => set({ date_format: e.target.value })} placeholder={t("auto")} />
        </Field>

        <Field label={t("Amount")} hint={t("One signed column, or a money-out and a money-in column.")}>
          <select
            value={draft.amount_kind}
            onChange={(e) =>
              set(
                e.target.value === "signed"
                  ? { amount_kind: "signed", debit: "", credit: "" }
                  : { amount_kind: "debit_credit", amount: "" },
              )
            }
          >
            <option value="signed">{t("One column, signed")}</option>
            <option value="debit_credit">{t("Separate out / in columns")}</option>
          </select>
        </Field>
        {draft.amount_kind === "signed" ? (
          <Field label={t("Amount column")}>{columnSelect(draft.amount, (amount) => set({ amount }), false)}</Field>
        ) : (
          <div className="import-mapping__pair">
            <Field label={t("Money out")}>{columnSelect(draft.debit, (debit) => set({ debit }))}</Field>
            <Field label={t("Money in")}>{columnSelect(draft.credit, (credit) => set({ credit }))}</Field>
          </div>
        )}

        <Field label={t("Decimal separator")}>
          <select value={draft.decimal} onChange={(e) => set({ decimal: e.target.value as "." | "," })}>
            <option value=".">{t("Full stop (1,234.56)")}</option>
            <option value=",">{t("Comma (1.234,56)")}</option>
          </select>
        </Field>
        <Field label={t("Description")} hint={t("What the payee match runs against; choose the most descriptive column.")}>
          {columnSelect((draft.description ?? [])[0] ?? "", (v) => set({ description: v ? [v] : [] }))}
        </Field>
        <Field label={t("Bank reference")} hint={t("The bank's own id for each line. With one, re-imports never double up.")}>
          {columnSelect(draft.reference, (reference) => set({ reference }))}
        </Field>
        <Field label={t("Extra memo")} hint={t("Kept verbatim as the transaction's memo.")}>
          {columnSelect(draft.memo, (memo) => set({ memo }))}
        </Field>
      </div>
      <Toggle
        label={t("Flip every sign")}
        hint={t("For a bank that lists a withdrawal as a positive number.")}
        checked={draft.invert}
        onChange={(invert) => set({ invert })}
      />

      {inspect.sample_rows.length > 0 && <SamplePreview rows={inspect.sample_rows} columns={columns} mapping={draft} />}

      <div className="actions">
        <button type="button" className="btn btn--primary" onClick={go} disabled={preview.isPending || !draft.date}>
          {preview.isPending ? t("Checking…") : t("Review the transactions")}
        </button>
        <button type="button" className="btn btn--link" onClick={onBack} disabled={preview.isPending}>
          {t("Choose another file")}
        </button>
      </div>
    </section>
  );
}

function namesFrom(rows: readonly Record<string, string>[]): string[] {
  return rows.length ? Object.keys(rows[0]!) : [];
}

/** The first few raw rows, with the mapped columns highlighted. */
function SamplePreview({
  rows,
  columns,
  mapping,
}: {
  rows: readonly Record<string, string>[];
  columns: readonly string[];
  mapping: ColumnMapping;
}) {
  const t = useT();
  const roleOf = (column: string): string | null => {
    if (column === mapping.date) return t("date");
    if (column === mapping.amount) return t("amount");
    if (column === mapping.debit) return t("out");
    if (column === mapping.credit) return t("in");
    if ((mapping.description ?? []).includes(column)) return t("description");
    if (column === mapping.reference) return t("reference");
    if (column === mapping.memo) return t("memo");
    return null;
  };
  return (
    <div className="import-sample">
      <table>
        <thead>
          <tr>
            {columns.map((c) => {
              const role = roleOf(c);
              return (
                <th key={c} className={role ? "is-mapped" : ""}>
                  {c}
                  {role && <span className="import-sample__role">{role}</span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 6).map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={roleOf(c) ? "is-mapped" : ""}>
                  {row[c] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ===========================================================================
// 3. review
// ===========================================================================
interface RowState {
  skip: boolean;
  payee: string;
  categoryId: string;
}

function ReviewStep({
  bookId,
  accountId,
  file,
  mapping,
  preview,
  accounts,
  payees,
  onBack,
  onImported,
}: {
  bookId: string;
  accountId: string;
  file: File;
  mapping: ColumnMapping;
  preview: CsvPreview;
  accounts: readonly Account[];
  payees: readonly Payee[];
  onBack: () => void;
  onImported: (report: StatementImport) => void;
}) {
  const t = useT();
  const commit = useCommitCsv(bookId, accountId);
  const saveMapping = useSaveImportMapping(bookId, accountId);
  const postable = useMemo(
    () => accounts.filter((a) => !a.placeholder && a.id !== accountId).sort((a, b) => a.path.localeCompare(b.path)),
    [accounts, accountId],
  );
  const payeeByKey = useMemo(() => new Map(payees.map((p) => [p.name.trim().toLowerCase(), p])), [payees]);

  const [rows, setRows] = useState<Record<string, RowState>>(() =>
    Object.fromEntries(
      preview.proposals.map((p) => [
        p.row_key,
        {
          skip: p.default_action === "skip",
          payee: p.suggested_payee_name,
          categoryId: p.suggested_category_id ?? "",
        },
      ]),
    ),
  );
  const [fallbackId, setFallbackId] = useState("");
  const set = (key: string, patch: Partial<RowState>) =>
    setRows((old) => ({ ...old, [key]: { ...old[key]!, ...patch } }));

  const toPost = preview.proposals.filter((p) => !rows[p.row_key]!.skip);
  const needFallback = toPost.some((p) => !rows[p.row_key]!.categoryId);
  const blocked = needFallback && !fallbackId;

  const decide = (): ImportDecision[] =>
    preview.proposals.map((p) => {
      const row = rows[p.row_key]!;
      if (row.skip) return { row_key: p.row_key, action: "skip" };
      const name = row.payee.trim();
      const existing = payeeByKey.get(name.toLowerCase());
      return {
        row_key: p.row_key,
        action: "post",
        payee_id: existing ? existing.id : null,
        new_payee: existing || !name ? "" : name,
        category_id: row.categoryId || null,
      };
    });

  const run = () => {
    saveMapping.mutate(mapping); // best-effort; the commit saves it too
    commit.mutate(
      { file, mapping, decisions: decide(), uncategorisedAccountId: fallbackId || undefined },
      { onSuccess: onImported },
    );
  };

  const counts = tally(preview.proposals);

  return (
    <section className="card import-review">
      <p className="muted">
        {t("{new} new, {duplicate} already imported, {transfer} transfers.", counts)}{" "}
        {t("Everything but the date and the amount is editable. Clear a payee to leave it blank.")}
      </p>
      {commit.error && <ErrorBanner error={commit.error} title={t("The import was refused")} />}

      <datalist id="import-payees">
        {payees.map((p) => (
          <option key={p.id} value={p.name} />
        ))}
      </datalist>

      <div className="import-review__table">
        <table>
          <thead>
            <tr>
              <th>{t("Post")}</th>
              <th>{t("Date")}</th>
              <th className="num">{t("Amount")}</th>
              <th>{t("Description")}</th>
              <th>{t("Payee")}</th>
              <th>{t("Category")}</th>
            </tr>
          </thead>
          <tbody>
            {preview.proposals.map((p) => {
              const row = rows[p.row_key]!;
              return (
                <tr key={p.row_key} className={row.skip ? "is-skipped" : `is-${p.status}`}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={t("Post this row")}
                      checked={!row.skip}
                      onChange={(e) => set(p.row_key, { skip: !e.target.checked })}
                    />
                  </td>
                  <td className="import-review__date">
                    {p.date}
                    <StatusTag proposal={p} />
                  </td>
                  <td className="num">
                    <Money amount={p.amount} />
                  </td>
                  <td className="import-review__desc" title={p.description}>
                    {p.description || <span className="muted">—</span>}
                  </td>
                  <td>
                    <input
                      list="import-payees"
                      aria-label={t("Payee for {description}", { description: p.description || p.date })}
                      value={row.payee}
                      disabled={row.skip}
                      onChange={(e) => set(p.row_key, { payee: e.target.value })}
                    />
                    {!row.skip && row.payee.trim() && !payeeByKey.has(row.payee.trim().toLowerCase()) && (
                      <span className="import-review__new">{t("new")}</span>
                    )}
                  </td>
                  <td>
                    <select
                      aria-label={t("Category for {description}", { description: p.description || p.date })}
                      value={row.categoryId}
                      disabled={row.skip}
                      onChange={(e) => set(p.row_key, { categoryId: e.target.value })}
                    >
                      <option value="">{t("— uncategorised —")}</option>
                      {postable.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.path}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {needFallback && (
        <Field
          label={t("Put uncategorised rows in")}
          hint={t("They stay visible in the register until the household files them.")}
        >
          <select value={fallbackId} onChange={(e) => setFallbackId(e.target.value)}>
            <option value="">{t("— choose an account —")}</option>
            {postable.map((a) => (
              <option key={a.id} value={a.id}>
                {a.path}
              </option>
            ))}
          </select>
        </Field>
      )}

      <div className="actions">
        <button
          type="button"
          className="btn btn--primary"
          onClick={run}
          disabled={commit.isPending || toPost.length === 0 || blocked}
        >
          {commit.isPending
            ? t("Importing…")
            : t("Import {n} transaction(s)", { n: toPost.length })}
        </button>
        <button type="button" className="btn btn--link" onClick={onBack} disabled={commit.isPending}>
          {t("Back to the columns")}
        </button>
      </div>
    </section>
  );
}

function StatusTag({ proposal }: { proposal: ImportProposal }) {
  const t = useT();
  if (proposal.status === "duplicate")
    return <span className="tag tag--muted" title={proposal.existing_transaction_id ?? ""}>{t("already imported")}</span>;
  if (proposal.status === "transfer")
    return (
      <span className="tag tag--info" title={proposal.existing_account_path}>
        {t("transfer — {account}", { account: proposal.existing_account_path })}
      </span>
    );
  return null;
}

function tally(proposals: readonly ImportProposal[]) {
  return {
    new: proposals.filter((p) => p.status === "new").length,
    duplicate: proposals.filter((p) => p.status === "duplicate").length,
    transfer: proposals.filter((p) => p.status === "transfer").length,
  };
}

// ===========================================================================
// 4. the report
// ===========================================================================
function ReportStep({
  report,
  registerHref,
  onAnother,
}: {
  report: StatementImport;
  registerHref: string;
  onAnother: () => void;
}) {
  const t = useT();
  const errors = report.problems.filter((p) => p.level === "ERROR");
  const notes = report.problems.filter((p) => p.level !== "ERROR");
  return (
    <section className="card" aria-label={t("Import report")}>
      <h2>{report.ok ? t("Imported") : t("Imported, with errors")}</h2>
      <p>{report.summary}</p>
      <dl className="import-report__stats">
        <div>
          <dt>{t("Posted")}</dt>
          <dd>{report.posted}</dd>
        </div>
        {report.payees_created > 0 && (
          <div>
            <dt>{t("New payees")}</dt>
            <dd>{report.payees_created}</dd>
          </div>
        )}
        {report.uncategorised > 0 && (
          <div>
            <dt>{t("Need a category")}</dt>
            <dd>{report.uncategorised}</dd>
          </div>
        )}
        {report.skipped_duplicates > 0 && (
          <div>
            <dt>{t("Already imported")}</dt>
            <dd>{report.skipped_duplicates}</dd>
          </div>
        )}
        {report.skipped_transfers > 0 && (
          <div>
            <dt>{t("Transfers skipped")}</dt>
            <dd>{report.skipped_transfers}</dd>
          </div>
        )}
      </dl>
      {errors.length > 0 && (
        <div className="banner banner--error" role="alert">
          <strong>{t("{n} row(s) could not be posted.", { n: errors.length })}</strong>
          <ul>
            {errors.map((p) => (
              <li key={p.message}>{p.message}</li>
            ))}
          </ul>
        </div>
      )}
      {notes.length > 0 && (
        <details>
          <summary>{t("{n} note(s)", { n: notes.length })}</summary>
          <ul>
            {notes.map((p) => (
              <li key={p.message}>{p.message}</li>
            ))}
          </ul>
        </details>
      )}
      <div className="actions">
        <Link to={registerHref} className="btn btn--primary">
          {t("Open the register")}
        </Link>
        <button type="button" className="btn btn--link" onClick={onAnother}>
          {t("Import another statement")}
        </button>
      </div>
    </section>
  );
}

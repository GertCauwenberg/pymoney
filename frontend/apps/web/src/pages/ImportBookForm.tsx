import { useT } from "@pymoney/core/hooks";
import type { ImportResult } from "@pymoney/core";
import { useImportKmymoney } from "@pymoney/core/hooks";
import { useState, type SubmitEvent } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Field, Toggle } from "../components/fields";

/**
 * Upload a KMyMoney file; the server makes the book and the uploader owns it.
 * The report is shown rather than summarised away: what was approximated,
 * skipped or adjusted is exactly what the user will otherwise discover one
 * register at a time.
 */
export function ImportBookForm({ onDone }: { onDone?: () => void }) {
  const t = useT();
  const importBook = useImportKmymoney();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [rounding, setRounding] = useState(true);
  const [prices, setPrices] = useState(true);

  const submit = (e: SubmitEvent) => {
    e.preventDefault();
    if (!file) return;
    importBook.mutate({ file, name: name.trim() || file.name.replace(/\.(kmy|xml)(\.gz)?$/i, ""), roundingAdjustments: rounding, skipPrices: !prices });
  };

  if (importBook.isSuccess) return <ImportReport result={importBook.data} onDone={onDone} />;

  return (
    <form className="card" onSubmit={submit} aria-label={t("Import from KMyMoney")}>
      <h2>{t("Import from KMyMoney")}</h2>
      <p className="muted">
        {t("Upload a")} <code>.kmy</code> {t("file (gzipped or plain XML). The server creates a new book from it, with you as its owner, and checks that every balance matches what KMyMoney showed. Scheduled transactions, budgets and reports are not imported; the report below says exactly what was left out.")}
      </p>
      {importBook.error && <ErrorBanner error={importBook.error} title={t("Could not import")} />}
      <Field label={t("File")}>
        <input
          type="file"
          accept=".kmy,.xml,.gz,application/octet-stream"
          onChange={(e) => {
            const chosen = e.target.files?.[0] ?? null;
            setFile(chosen);
            if (chosen && !name) setName(chosen.name.replace(/\.(kmy|xml)(\.gz)?$/i, ""));
          }}
        />
      </Field>
      <Field label={t("Book name")}>
        <input type="text" maxLength={255} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <div className="toolbar__toggles">
        <Toggle
          label={t("Post rounding corrections")}
          hint={t("KMyMoney keeps exact fractions; PyMoney books whole cents. Where they differ, a correcting entry makes every balance match.")}
          checked={rounding}
          onChange={setRounding}
        />
        <Toggle label={t("Import the price history")} checked={prices} onChange={setPrices} />
      </div>
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={!file || importBook.isPending}>
          {importBook.isPending ? t("Importing…") : t("Import")}
        </button>
        {onDone && (
          <button type="button" className="btn btn--link" onClick={onDone} disabled={importBook.isPending}>
            {t("Cancel")}
          </button>
        )}
      </div>
    </form>
  );
}

function ImportReport({ result, onDone }: { result: ImportResult; onDone?: (() => void) | undefined }) {
  const t = useT();
  const errors = result.problems.filter((p) => p.level === "ERROR");
  const warnings = result.problems.filter((p) => p.level === "WARNING");
  return (
    <section className="card" aria-label={t("Import report")}>
      <h2>{result.ok ? t("Imported") : t("Imported, with errors")}</h2>
      <p>
        <strong>{result.book.name}</strong> is ready:{" "}
        {Object.entries(result.imported)
          .map(([what, n]) => `${n} ${what}`)
          .join(", ")}
        .
      </p>
      {Object.keys(result.skipped).length > 0 && (
        <p className="muted">
          Not imported (PyMoney has no home for them yet):{" "}
          {Object.entries(result.skipped)
            .map(([what, n]) => `${n} ${what}`)
            .join(", ")}
          .
        </p>
      )}
      {result.adjustments.length > 0 && (
        <details>
          <summary>{result.adjustments.length} rounding correction(s)</summary>
          <ul>
            {result.adjustments.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </details>
      )}
      {errors.length > 0 && (
        <div className="banner banner--error" role="alert">
          <strong>{errors.length} transaction(s) could not be imported.</strong>
          <ul>
            {errors.map((p) => (
              <li key={`${p.category}-${p.source_id}-${p.message}`}>
                {p.message}
                {p.source_id && <code> ({p.source_id})</code>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <details>
          <summary>{warnings.length} warning(s)</summary>
          <ul>
            {warnings.map((p) => (
              <li key={`${p.category}-${p.source_id}-${p.message}`}>
                {p.message}
                {p.source_id && <code> ({p.source_id})</code>}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="actions">
        <Link to={`/books/${result.book.id}`} className="btn btn--primary">
          Open {result.book.name}
        </Link>
        {onDone && (
          <button type="button" className="btn btn--link" onClick={onDone}>
            {t("Close")}
          </button>
        )}
      </div>
    </section>
  );
}

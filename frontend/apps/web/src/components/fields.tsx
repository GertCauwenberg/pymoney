import { isIsoDate } from "@pymoney/core";
import type { Commodity } from "@pymoney/core";
import { useEffect, useState, type ReactNode } from "react";

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string | undefined }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}

/**
 * A date input that only reports complete, valid dates. Typing in a date
 * field produces intermediate garbage; refetching a report on every keystroke
 * of that is pointless, so the parent hears only about real dates.
 */
export function DateField({
  label,
  value,
  onChange,
  max,
  min,
}: {
  label: string;
  value: string;
  onChange: (iso: string) => void;
  max?: string | undefined;
  min?: string | undefined;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <Field label={label}>
      <input
        type="date"
        value={draft}
        min={min}
        max={max}
        onChange={(e) => {
          setDraft(e.target.value);
          if (isIsoDate(e.target.value)) onChange(e.target.value);
        }}
      />
    </Field>
  );
}

export function CurrencyField({
  label = "Report in",
  currencies,
  value,
  baseCurrencyId,
  onChange,
}: {
  label?: string;
  currencies: readonly Commodity[] | undefined;
  /** A commodity id, or "" for the book's own currency. */
  value: string;
  baseCurrencyId: string | null | undefined;
  onChange: (id: string) => void;
}) {
  const base = currencies?.find((c) => c.id === baseCurrencyId);
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={!currencies}>
        <option value="">{base ? `${base.code} (book currency)` : "Book currency"}</option>
        {currencies
          ?.filter((c) => c.id !== baseCurrencyId)
          .map((c) => (
            <option key={c.id} value={c.id}>
              {c.code}
              {c.name && c.name !== c.code ? ` — ${c.name}` : ""}
            </option>
          ))}
      </select>
    </Field>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: string;
}) {
  return (
    <label className="toggle" title={hint}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

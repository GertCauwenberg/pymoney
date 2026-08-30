import { useT } from "@pymoney/core/hooks";
import { isIsoDate } from "@pymoney/core";
import type { Commodity } from "@pymoney/core";
import { cloneElement, isValidElement, useEffect, useId, useState, type ReactElement, type ReactNode } from "react";

/**
 * Label + control + optional hint. The hint is wired up with
 * `aria-describedby` rather than nested in the label, so the control's
 * accessible name is just the label ("Code", not "Code ISO 4217, e.g. INR").
 */
export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string | undefined }) {
  const id = useId();
  const hintId = `${id}-hint`;
  const control = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        id,
        ...(hint ? { "aria-describedby": hintId } : {}),
      })
    : children;
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {control}
      {hint && (
        <span className="field__hint" id={hintId}>
          {hint}
        </span>
      )}
    </div>
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
  allowEmpty = false,
}: {
  label: string;
  value: string;
  onChange: (iso: string) => void;
  max?: string | undefined;
  min?: string | undefined;
  /** Report an empty string when the field is cleared ("no bound"). */
  allowEmpty?: boolean;
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
          if (isIsoDate(e.target.value) || (allowEmpty && e.target.value === "")) onChange(e.target.value);
        }}
      />
    </Field>
  );
}

export function CurrencyField({
  label,
  currencies,
  value,
  baseCurrencyId,
  onChange,
}: {
  label?: string | undefined;
  currencies: readonly Commodity[] | undefined;
  /** A commodity id, or "" for the book's own currency. */
  value: string;
  baseCurrencyId: string | null | undefined;
  onChange: (id: string) => void;
}) {
  const t = useT();
  const base = currencies?.find((c) => c.id === baseCurrencyId);
  return (
    <Field label={label ?? t("Report in")}>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={!currencies}>
        <option value="">{base ? t("{code} (book currency)", { code: base.code }) : t("Book currency")}</option>
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

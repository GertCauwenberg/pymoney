import { useCreateBook } from "@pymoney/core/hooks";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Field } from "../components/fields";

/** The usual suspects, so most people never type a currency code. */
const CURRENCIES: { code: string; name: string; symbol: string; scale: number }[] = [
  { code: "EUR", name: "Euro", symbol: "€", scale: 2 },
  { code: "USD", name: "US Dollar", symbol: "$", scale: 2 },
  { code: "GBP", name: "Pound Sterling", symbol: "£", scale: 2 },
  { code: "CHF", name: "Swiss Franc", symbol: "CHF", scale: 2 },
  { code: "JPY", name: "Yen", symbol: "¥", scale: 0 },
  { code: "CAD", name: "Canadian Dollar", symbol: "$", scale: 2 },
  { code: "AUD", name: "Australian Dollar", symbol: "$", scale: 2 },
  { code: "SEK", name: "Swedish Krona", symbol: "kr", scale: 2 },
  { code: "NOK", name: "Norwegian Krone", symbol: "kr", scale: 2 },
  { code: "DKK", name: "Danish Krone", symbol: "kr", scale: 2 },
  { code: "PLN", name: "Złoty", symbol: "zł", scale: 2 },
  { code: "CZK", name: "Czech Koruna", symbol: "Kč", scale: 2 },
];

const OTHER = "__other__";

export function NewBookForm({ onDone, first = false }: { onDone?: () => void; first?: boolean }) {
  const create = useCreateBook();
  const navigate = useNavigate();
  const [name, setName] = useState(first ? "Household" : "");
  const [choice, setChoice] = useState("EUR");
  const [code, setCode] = useState("");
  const [currencyName, setCurrencyName] = useState("");
  const [scale, setScale] = useState(2);

  const custom = choice === OTHER;
  const picked = CURRENCIES.find((c) => c.code === choice);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = custom
      ? {
          name: name.trim(),
          base_currency_code: code.trim().toUpperCase(),
          base_currency_name: currencyName.trim(),
          base_currency_scale: scale,
        }
      : {
          name: name.trim(),
          base_currency_code: picked!.code,
          base_currency_name: picked!.name,
          base_currency_symbol: picked!.symbol,
          base_currency_scale: picked!.scale,
        };
    create.mutate(body, {
      onSuccess: (book) => {
        onDone?.();
        void navigate(`/books/${book.id}`);
      },
    });
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>{first ? "Create your first book" : "New book"}</h2>
      <p className="muted">
        A book is one set of accounts — a household, a club, a small business. Everything in it is
        kept in its base currency; other currencies and securities can be added later.
      </p>
      {create.error && <ErrorBanner error={create.error} title="Could not create the book" />}
      <Field label="Name">
        <input type="text" required maxLength={255} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>
      <Field label="Base currency">
        <select value={choice} onChange={(e) => setChoice(e.target.value)}>
          {CURRENCIES.map((c) => (
            <option key={c.code} value={c.code}>
              {c.code} — {c.name}
            </option>
          ))}
          <option value={OTHER}>Other…</option>
        </select>
      </Field>
      {custom && (
        <div className="field-row">
          <Field label="Code" hint="ISO 4217, e.g. INR">
            <input type="text" required minLength={1} maxLength={32} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Field label="Name">
            <input type="text" value={currencyName} onChange={(e) => setCurrencyName(e.target.value)} />
          </Field>
          <Field label="Decimal places" hint="2 for most; 0 for JPY; 3 for KWD">
            <input type="number" min={0} max={12} value={scale} onChange={(e) => setScale(Number(e.target.value))} />
          </Field>
        </div>
      )}
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={create.isPending || !name.trim()}>
          {create.isPending ? "…" : "Create book"}
        </button>
        {onDone && (
          <button type="button" className="btn btn--link" onClick={onDone}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

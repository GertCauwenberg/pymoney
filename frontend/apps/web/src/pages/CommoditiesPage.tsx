import { useT } from "@pymoney/core/hooks";
import { decimalsOf } from "@pymoney/core";
import type { Account, Commodity, CommodityKind } from "@pymoney/core";
import { useAccounts, useChangeCommodity, useCommodities, useQuoteProviders } from "@pymoney/core/hooks";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field } from "../components/fields";
import { useBookContext } from "../reports/useReportParams";

const KINDS: { id: CommodityKind; label: string }[] = [
  { id: "CURRENCY", label: "Currency" },
  { id: "STOCK", label: "Stock" },
  { id: "FUND", label: "Fund" },
  { id: "BOND", label: "Bond" },
  { id: "CRYPTO", label: "Crypto" },
  { id: "OTHER", label: "Other" },
];

const CAN_WRITE = new Set(["editor", "owner"]);

/** Accounts per commodity, so the list can say why a delete would be refused. */
function usage(accounts: readonly Account[] | undefined): Map<string, number> {
  const out = new Map<string, number>();
  for (const a of accounts ?? []) out.set(a.commodity_id, (out.get(a.commodity_id) ?? 0) + 1);
  return out;
}

/**
 * Currencies and securities, uniformly (the server's decision #2). Editors and
 * owners add, edit and delete; the minor unit factor is fixed once a commodity
 * exists, because every booked amount is a count of it, and the server refuses
 * to delete a commodity that an account or a price still uses.
 */
export function CommoditiesPage() {
  const t = useT();
  const { bookId, baseCurrencyId, role } = useBookContext();
  const commodities = useCommodities(bookId);
  const accounts = useAccounts(bookId, { include_closed: true });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const canWrite = role !== null && CAN_WRITE.has(role);

  const used = useMemo(() => usage(accounts.data), [accounts.data]);
  const sorted = useMemo(
    () =>
      [...(commodities.data ?? [])].sort(
        (a, b) => (a.id === baseCurrencyId ? -1 : b.id === baseCurrencyId ? 1 : 0) || a.kind.localeCompare(b.kind) || a.code.localeCompare(b.code),
      ),
    [commodities.data, baseCurrencyId],
  );
  const editingCommodity = commodities.data?.find((c) => c.id === editing);

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../reports/balance-sheet">{t("Reports")}</Link> › Commodities
          </p>
          <h1>{t("Commodities")}</h1>
          <p className="muted">
            {t("Currencies and securities. The number of minor units per unit is fixed when a commodity is created; the rest can change at any time.")}
          </p>
        </div>
        <div className="actions">
          {canWrite && !creating && (
            <button type="button" className="btn btn--primary" onClick={() => setCreating(true)}>
              {t("New commodity")}
            </button>
          )}
        </div>
      </header>

      {commodities.isPending && <Loading />}
      {commodities.isError && <ErrorBanner error={commodities.error} onRetry={() => void commodities.refetch()} />}

      {creating && <CommodityEditor bookId={bookId} onDone={() => setCreating(false)} />}
      {canWrite && editingCommodity && (
        <CommodityEditor bookId={bookId} existing={editingCommodity} onDone={() => setEditing(null)} />
      )}

      {commodities.data && (
        <table className="report commodities">
          <thead>
            <tr>
              <th scope="col">{t("Code")}</th>
              <th scope="col">{t("Name")}</th>
              <th scope="col">{t("Kind")}</th>
              <th scope="col">{t("Symbol")}</th>
              <th scope="col" className="num">
                {t("Decimals")}
              </th>
              <th scope="col" className="num">
                {t("Minor units")}
              </th>
              <th scope="col">{t("ISIN")}</th>
              <th scope="col">{t("Quotes")}</th>
              <th scope="col" className="num">
                {t("Accounts")}
              </th>
              {canWrite && <th scope="col" />}
            </tr>
          </thead>
          <tbody>
            {sorted.map((c) => (
              <CommodityRow
                key={c.id}
                commodity={c}
                isBase={c.id === baseCurrencyId}
                accounts={used.get(c.id) ?? 0}
                canWrite={canWrite}
                bookId={bookId}
                onEdit={() => setEditing(c.id)}
              />
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}

function CommodityRow({
  commodity,
  isBase,
  accounts,
  canWrite,
  bookId,
  onEdit,
}: {
  commodity: Commodity;
  isBase: boolean;
  accounts: number;
  canWrite: boolean;
  bookId: string;
  onEdit: () => void;
}) {
  const t = useT();
  const change = useChangeCommodity(bookId);
  const [confirming, setConfirming] = useState(false);
  const blocked = isBase ? t("The book's base currency cannot be deleted.") : accounts > 0 ? `Used by ${accounts} account(s).` : undefined;
  return (
    <tr>
      <td>
        <strong>{commodity.code}</strong>
        {isBase && <span className="muted"> · base currency</span>}
      </td>
      <td>{commodity.name}</td>
      <td className="muted">{commodity.kind.toLowerCase()}</td>
      <td>{commodity.symbol ?? <span className="muted">—</span>}</td>
      <td className="num">{commodity.display_precision}</td>
      <td className="num" title={`1/${commodity.minor_unit_factor} of a unit is the smallest amount that can be booked`}>
        {commodity.minor_unit_factor}
      </td>
      <td className="muted">{commodity.isin ?? ""}</td>
      <td className="muted">{commodity.quote_source ?? "manual"}</td>
      <td className="num">{accounts}</td>
      {canWrite && (
        <td className="num">
          {change.error && <ErrorBanner error={change.error} title={t("Could not delete")} />}
          {confirming ? (
            <span className="actions actions--inline">
              <span className="muted">Delete {commodity.code}?</span>
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={change.isPending}
                onClick={() => change.mutate({ kind: "delete", commodityId: commodity.id }, { onSettled: () => setConfirming(false) })}
              >
                {t("Yes, delete")}
              </button>
              <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
                {t("No")}
              </button>
            </span>
          ) : (
            <span className="actions actions--inline">
              <button type="button" className="btn btn--small" onClick={onEdit} aria-label={`Edit ${commodity.code}`}>
                {t("Edit")}
              </button>
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={blocked !== undefined}
                title={blocked}
                onClick={() => setConfirming(true)}
                aria-label={`Delete ${commodity.code}`}
              >
                {t("Delete")}
              </button>
            </span>
          )}
        </td>
      )}
    </tr>
  );
}

function CommodityEditor({ bookId, existing, onDone }: { bookId: string; existing?: Commodity; onDone: () => void }) {
  const t = useT();
  const change = useChangeCommodity(bookId);
  const providers = useQuoteProviders();
  const [code, setCode] = useState(existing?.code ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<CommodityKind>(existing?.kind ?? "CURRENCY");
  const [symbol, setSymbol] = useState(existing?.symbol ?? "");
  const [scale, setScale] = useState(existing ? decimalsOf(existing.minor_unit_factor) : 2);
  const [precision, setPrecision] = useState<string>(existing ? String(existing.display_precision) : "");
  const [isin, setIsin] = useState(existing?.isin ?? "");
  const [quoteSource, setQuoteSource] = useState(existing?.quote_source ?? "");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!existing) return;
    setCode(existing.code);
    setName(existing.name);
    setKind(existing.kind);
    setSymbol(existing.symbol ?? "");
    setScale(decimalsOf(existing.minor_unit_factor));
    setPrecision(String(existing.display_precision));
    setIsin(existing.isin ?? "");
    setQuoteSource(existing.quote_source ?? "");
    setError(null);
  }, [existing]);

  // A security defaults to four decimals (fractional shares); a currency to two.
  const onKind = (next: CommodityKind) => {
    setKind(next);
    if (!existing) setScale(next === "CURRENCY" ? 2 : 4);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const precisionValue = precision.trim() === "" ? null : Number(precision);
    try {
      if (!existing) {
        await change.mutateAsync({
          kind: "create",
          body: {
            code: code.trim().toUpperCase(),
            name: name.trim(),
            kind,
            scale,
            minor_unit_factor: null,
            display_precision: precisionValue,
            symbol: symbol.trim() || null,
            isin: isin.trim() || null,
            quote_source: quoteSource.trim() || null,
          },
        });
      } else {
        const body: Record<string, string | number | null> = {};
        if (code.trim().toUpperCase() !== existing.code) body["code"] = code.trim().toUpperCase();
        if (name.trim() !== existing.name) body["name"] = name.trim();
        if ((symbol.trim() || null) !== existing.symbol) body["symbol"] = symbol.trim() || null;
        if (precisionValue !== existing.display_precision) body["display_precision"] = precisionValue;
        if ((isin.trim() || null) !== existing.isin) body["isin"] = isin.trim() || null;
        if ((quoteSource.trim() || null) !== existing.quote_source) body["quote_source"] = quoteSource.trim() || null;
        if (Object.keys(body).length > 0) await change.mutateAsync({ kind: "patch", commodityId: existing.id, body });
      }
      onDone();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <form className="card account-editor" onSubmit={(e) => void submit(e)} aria-label={existing ? `Edit ${existing.code}` : t("New commodity")}>
      <h2>{existing ? `Edit ${existing.code}` : t("New commodity")}</h2>
      {error !== null && <ErrorBanner error={error} title={t("Could not save")} />}
      <div className="field-row">
        <Field label={t("Code")} hint={t("ISO 4217 for a currency, the ticker for a security. Unique in the book.")}>
          <input type="text" required maxLength={32} value={code} onChange={(e) => setCode(e.target.value)} className="input--short" />
        </Field>
        <Field label={t("Name")}>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t("Kind")} hint={existing ? t("Fixed once the commodity exists.") : undefined}>
          <select value={kind} disabled={!!existing} onChange={(e) => onKind(e.target.value as CommodityKind)}>
            {KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {t(k.label)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Symbol")} hint={t("Shown instead of the code, e.g. €")}>
          <input type="text" maxLength={16} value={symbol} onChange={(e) => setSymbol(e.target.value)} className="input--short" />
        </Field>
      </div>
      <div className="field-row">
        <Field
          label={t("Smallest unit (decimals)")}
          hint={
            existing
              ? "Fixed for life: every booked amount is a count of this unit."
              : "2 for EUR, 0 for JPY, 4 for shares traded in fractions. Cannot be changed later."
          }
        >
          <input
            type="number"
            min={0}
            max={12}
            required
            disabled={!!existing}
            value={scale}
            onChange={(e) => setScale(Number(e.target.value))}
            className="input--short"
          />
        </Field>
        <Field label={t("Display decimals")} hint={t("Blank: the same as the smallest unit.")}>
          <input type="number" min={0} max={12} value={precision} onChange={(e) => setPrecision(e.target.value)} className="input--short" />
        </Field>
        <Field label={t("ISIN")}>
          <input type="text" maxLength={32} value={isin} onChange={(e) => setIsin(e.target.value)} />
        </Field>
        <Field
          label={t("Quote source")}
          hint={
            providers.data?.find((p) => p.name === quoteSource)?.description ??
            "Where the server fetches prices from, on its schedule. Manual: you enter them."
          }
        >
          <select value={quoteSource} onChange={(e) => setQuoteSource(e.target.value)} disabled={providers.isPending}>
            <option value="">{t("Manual (no fetching)")}</option>
            {providers.data?.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
            {/* A source the server no longer has a provider for still shows, so it can be cleared. */}
            {quoteSource && providers.data && !providers.data.some((p) => p.name === quoteSource) && (
              <option value={quoteSource}>{quoteSource} (not installed)</option>
            )}
          </select>
        </Field>
      </div>
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={change.isPending || !code.trim()}>
          {change.isPending ? "…" : existing ? t("Save") : t("Create commodity")}
        </button>
        <button type="button" className="btn" onClick={onDone} disabled={change.isPending}>
          {t("Cancel")}
        </button>
      </div>
    </form>
  );
}

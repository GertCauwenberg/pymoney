import { useT } from "@pymoney/core/hooks";
import { formatRate, isIsoDate, today } from "@pymoney/core";
import type { Commodity, Price, QuoteRun } from "@pymoney/core";
import {
  useChangePrice,
  useCommodities,
  useFetchQuotes,
  useLastQuoteRun,
  usePrices,
  useQuoteProviders,
} from "@pymoney/core/hooks";
import { useMemo, useState, type SubmitEvent } from "react";
import { Link } from "react-router-dom";

import { ErrorBanner } from "../components/ErrorBanner";
import { Loading } from "../components/Loading";
import { Field, DateField } from "../components/fields";
import { useBookContext, useReportParams } from "../reports/useReportParams";

const CAN_WRITE = new Set(["editor", "owner"]);
const DEFAULTS = { commodity: "", start: "", end: "" };

/**
 * Price history: what the server fetched, what the user typed, and the tools
 * for both. A hand-entered price is stored with source "user" and outranks a
 * fetched quote for the same day, so correcting a bad quote means adding the
 * right figure, not deleting the wrong one (though that works too).
 */
export function PricesPage() {
  const t = useT();
  const { bookId, baseCurrencyId, role } = useBookContext();
  const [params, update] = useReportParams(DEFAULTS);
  const commodities = useCommodities(bookId);
  const providers = useQuoteProviders();
  const lastRun = useLastQuoteRun(bookId);
  const fetchQuotes = useFetchQuotes(bookId);
  const canWrite = role !== null && CAN_WRITE.has(role);
  const [adding, setAdding] = useState(false);
  const [backfillFrom, setBackfillFrom] = useState("");

  // The filter is "involving this commodity", either side of the pair; the
  // server filters one side at a time, so ask for both and merge.
  const query = useMemo(() => ({
    ...(params.commodity ? { base_id: params.commodity } : {}),
    ...(params.start ? { on_or_after: params.start } : {}),
    ...(params.end ? { on_or_before: params.end } : {})
    }), 
    [params.commodity, params.start, params.end]);
  const asQuote = useMemo(() => ({
    ...(params.commodity ? { quote_id: params.commodity } : {}),
    ...(params.start ? { on_or_after: params.start } : {}),
    ...(params.end ? { on_or_before: params.end } : {}) 
    }), 
    [params.commodity, params.start, params.end]);
  const prices = usePrices(bookId, query);
  const pricesAsQuote = usePrices(bookId, asQuote);

  const rows = useMemo(() => {
    const seen = new Map<string, Price>();
    for (const page of prices.data?.pages ?? []) for (const p of page.items) seen.set(p.id, p);
    if (params.commodity) for (const page of pricesAsQuote.data?.pages ?? []) for (const p of page.items) seen.set(p.id, p);
    return [...seen.values()].sort((a, b) => b.effective_date.localeCompare(a.effective_date) || b.as_of.localeCompare(a.as_of));
  }, [prices.data, pricesAsQuote.data, params.commodity]);

  const fetchable = (commodities.data ?? []).filter((c) => c.quote_source && c.id !== baseCurrencyId);
  const run = fetchQuotes.data ?? lastRun.data ?? null;

  const fetchNow = () => {
    const body = backfillFrom && isIsoDate(backfillFrom) ? { start: backfillFrom } : {};
    fetchQuotes.mutate(body);
  };

  return (
    <article className="report-page">
      <header className="report-page__header">
        <div>
          <p className="breadcrumbs muted">
            <Link to="../../reports/balance-sheet">{t("Reports")}</Link> › {t("Settings")} › {t("Prices")}
          </p>
          <h1>{t("Prices")}</h1>
          <p className="muted">
            {t("Exchange rates and security prices, each with its source and the moment it became known. A price you enter wins over a fetched quote for the same day.")}
          </p>
        </div>
        <div className="actions">
          {canWrite && !adding && (
            <button type="button" className="btn btn--primary" onClick={() => setAdding(true)}>
              {t("Add price")}
            </button>
          )}
        </div>
      </header>

      {canWrite && (
        <section className="card quotes">
          <h2>{t("Fetching")}</h2>
          {fetchable.length === 0 ? (
            <p className="muted">
              {t("No commodity has a quote source yet. Set one in")} <Link to="../commodities">{t("Commodities")}</Link> (
              {providers.data?.map((p) => p.name).join(", ") || "…"}) and the server will fetch prices on its schedule.
            </p>
          ) : (
            <p className="muted">
              Fetched on a schedule for {fetchable.map((c) => `${c.code} (${c.quote_source})`).join(", ")}. Fetch now to
              pick up today&apos;s quotes, or backfill from a date to fill in history.
            </p>
          )}
          <div className="field-row quotes__controls">
            <Field label={t("Backfill from")} hint={t("Leave empty for what is new since the last fetch.")}>
              <input type="date" value={backfillFrom} max={today()} onChange={(e) => setBackfillFrom(e.target.value)} />
            </Field>
            <div className="actions">
              <button type="button" className="btn" disabled={fetchQuotes.isPending || fetchable.length === 0} onClick={fetchNow}>
                {fetchQuotes.isPending ? t("Fetching…") : backfillFrom ? t("Backfill") : t("Fetch now")}
              </button>
            </div>
          </div>
          {fetchQuotes.error && <ErrorBanner error={fetchQuotes.error} title={t("Could not fetch")} />}
          {run && <RunReport run={run} fresh={fetchQuotes.data === run} />}
        </section>
      )}

      {adding && commodities.data && (
        <PriceForm bookId={bookId} commodities={commodities.data} baseCurrencyId={baseCurrencyId} onDone={() => setAdding(false)} />
      )}

      <form className="toolbar" onSubmit={(e) => e.preventDefault()}>
        <Field label={t("Commodity")}>
          <select value={params.commodity} onChange={(e) => update({ commodity: e.target.value })}>
            <option value="">{t("All")}</option>
            {(commodities.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <DateField label={t("From")} value={params.start} max={params.end || undefined} onChange={(start) => update({ start })} allowEmpty />
        <DateField label={t("To")} value={params.end} min={params.start || undefined} onChange={(end) => update({ end })} allowEmpty />
      </form>

      {prices.isError && <ErrorBanner error={prices.error} onRetry={() => void prices.refetch()} />}
      {prices.isPending && <Loading />}
      {prices.data && (
        <>
          <table className="report prices">
            <thead>
              <tr>
                <th scope="col">{t("Date")}</th>
                <th scope="col">{t("Pair")}</th>
                <th scope="col" className="num">
                  {t("Rate")}
                </th>
                <th scope="col">{t("Source")}</th>
                <th scope="col">{t("Known since")}</th>
                {canWrite && <th scope="col" />}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={canWrite ? 6 : 5} className="empty">
                    {t("No prices yet.")}
                  </td>
                </tr>
              )}
              {rows.map((p) => (
                <PriceRow key={p.id} price={p} bookId={bookId} canWrite={canWrite} />
              ))}
            </tbody>
          </table>
          {(prices.hasNextPage || (params.commodity && pricesAsQuote.hasNextPage)) && (
            <div className="load-more">
              <button
                type="button"
                className="btn"
                disabled={prices.isFetchingNextPage || pricesAsQuote.isFetchingNextPage}
                onClick={() => {
                  if (prices.hasNextPage) void prices.fetchNextPage();
                  if (params.commodity && pricesAsQuote.hasNextPage) void pricesAsQuote.fetchNextPage();
                }}
              >
                {t("Load more")}
              </button>
            </div>
          )}
        </>
      )}
    </article>
  );
}

function RunReport({ run, fresh }: { run: QuoteRun; fresh: boolean }) {
  const t = useT();
  return (
    <div className={`banner ${run.ok ? "banner--info" : "banner--warning"} quotes__report`} role="status">
      <strong>{fresh ? t("Just now") : `Last run ${new Date(run.started_at).toLocaleString()}`}.</strong> {run.summary.split(";")[0]}
      {run.problems.length > 0 && (
        <ul className="quotes__problems">
          {run.problems.map((p) => (
            <li key={`${p.provider}-${p.commodity}-${p.message}`}>
              <strong>{p.commodity}</strong> via {p.provider}: {p.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PriceRow({ price, bookId, canWrite }: { price: Price; bookId: string; canWrite: boolean }) {
  const t = useT();
  const change = useChangePrice(bookId);
  const [confirming, setConfirming] = useState(false);
  return (
    <tr className={price.source === "user" ? "prices__row--user" : undefined}>
      <td>{price.effective_date}</td>
      <td>
        1 {price.base} in {price.quote}
      </td>
      <td className="num" title={`${price.rate.num}/${price.rate.den}`}>
        {formatRate(price.rate, 6)}
      </td>
      <td className="muted">{price.source}</td>
      <td className="muted">{new Date(price.as_of).toLocaleString()}</td>
      {canWrite && (
        <td className="num">
          {change.error && <ErrorBanner error={change.error} title={t("Could not delete")} />}
          {confirming ? (
            <span className="actions actions--inline">
              <button
                type="button"
                className="btn btn--small btn--danger"
                disabled={change.isPending}
                onClick={() => change.mutate({ kind: "delete", priceId: price.id }, { onSettled: () => setConfirming(false) })}
              >
                {t("Yes, delete")}
              </button>
              <button type="button" className="btn btn--small" onClick={() => setConfirming(false)}>
                {t("No")}
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="btn btn--small btn--danger"
              aria-label={`Delete ${price.base}/${price.quote} ${price.effective_date} ${price.source}`}
              onClick={() => setConfirming(true)}
            >
              {t("Delete")}
            </button>
          )}
        </td>
      )}
    </tr>
  );
}

const RATE = /^\d+(\.\d+)?$|^\d+\/\d+$/;

function PriceForm({
  bookId,
  commodities,
  baseCurrencyId,
  onDone,
}: {
  bookId: string;
  commodities: readonly Commodity[];
  baseCurrencyId: string | null;
  onDone: () => void;
}) {
  const t = useT();
  const change = useChangePrice(bookId);
  const others = commodities.filter((c) => c.id !== baseCurrencyId);
  const [baseId, setBaseId] = useState(others[0]?.id ?? "");
  const [quoteId, setQuoteId] = useState(baseCurrencyId ?? "");
  const [rate, setRate] = useState("");
  const [date, setDate] = useState(today());
  const base = commodities.find((c) => c.id === baseId);
  const quote = commodities.find((c) => c.id === quoteId);
  const valid = RATE.test(rate.trim()) && baseId && quoteId && baseId !== quoteId && isIsoDate(date);

  const submit = (e: SubmitEvent) => {
    e.preventDefault();
    if (!valid) return;
    change.mutate(
      { kind: "create", body: { base_id: baseId, quote_id: quoteId, rate: rate.trim(), effective_date: date, as_of: null, source: "user" } },
      { onSuccess: onDone },
    );
  };

  return (
    <form className="card account-editor" onSubmit={submit} aria-label={t("Add price")}>
      <h2>{t("Add price")}</h2>
      {change.error && <ErrorBanner error={change.error} title={t("Could not save")} />}
      <div className="field-row">
        <Field label={t("Commodity")} hint={t("What is being priced")}>
          <select value={baseId} onChange={(e) => setBaseId(e.target.value)}>
            {commodities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("In")} hint={base && quote ? `1 ${base.code} = … ${quote.code}` : undefined}>
          <select value={quoteId} onChange={(e) => setQuoteId(e.target.value)}>
            {commodities.map((c) => (
              <option key={c.id} value={c.id}>
                {c.code}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("Rate")} hint={t("A decimal, or an exact ratio like 10/9. Never rounded.")}>
          <input type="text" inputMode="decimal" required value={rate} onChange={(e) => setRate(e.target.value)} className="input--amount" />
        </Field>
        <Field label={t("Date")}>
          <input type="date" required value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>
      {baseId && baseId === quoteId && (
        <p className="field__error" role="alert">
          {t("A price relates two different commodities.")}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="btn btn--primary" disabled={change.isPending || !valid}>
          {change.isPending ? "…" : t("Add price")}
        </button>
        <button type="button" className="btn" onClick={onDone} disabled={change.isPending}>
          {t("Cancel")}
        </button>
      </div>
    </form>
  );
}

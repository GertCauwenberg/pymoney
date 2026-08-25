import { formatRate } from "@pymoney/core";
import type { Conversion } from "@pymoney/core";

/**
 * How the numbers came to be: every rate the server used, with its sources,
 * and whatever it could not value. A total you cannot trace is a guess.
 */
export function ValuationPanel({
  conversions,
  unpriced,
  target,
}: {
  conversions: readonly Conversion[];
  unpriced: readonly string[];
  target: string;
}) {
  if (conversions.length === 0 && unpriced.length === 0) return null;
  return (
    <section className="valuation">
      {unpriced.length > 0 && (
        <div className="banner banner--warning" role="status">
          <strong>Left out of the totals:</strong> {unpriced.join(", ")} — no usable price in {target}.
        </div>
      )}
      {conversions.length > 0 && (
        <details>
          <summary>
            {conversions.length} exchange rate{conversions.length === 1 ? "" : "s"} used
          </summary>
          <table className="report report--compact">
            <thead>
              <tr>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col" className="num">
                  Rate
                </th>
                <th scope="col">Via</th>
                <th scope="col">Sources</th>
              </tr>
            </thead>
            <tbody>
              {conversions.map((c) => (
                <tr key={`${c.base}-${c.quote}`}>
                  <td>{c.base}</td>
                  <td>{c.quote}</td>
                  <td className="num" title={`${c.rate.num}/${c.rate.den}`}>
                    {formatRate(c.rate)}
                  </td>
                  <td>{c.hops > 1 ? c.path.join(" → ") : "direct"}</td>
                  <td>{c.sources.join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </section>
  );
}

/**
 * Two charts in plain SVG: a pie and a line. No charting library, because two
 * shapes do not justify one, and because every number drawn here comes from
 * exact integers formatted with the same `formatMinor` the tables use -- the
 * legend next to a wedge says exactly what the report row says.
 *
 * Both are accessible by construction: the pie is a list of slices with an
 * `<title>` per wedge and a visible legend; the line carries a data table
 * behind it for anyone who cannot see the curve.
 */
import { formatMinor } from "@pymoney/core";
import type { PieSlice } from "@pymoney/core";
import { useT } from "@pymoney/core/hooks";
import { useState } from "react";
import { Link } from "react-router-dom";

/** The Okabe-Ito palette: built to stay apart for the common kinds of
 * colour-blindness, and it still does on a white page and in print. Darkened
 * yellow, because Okabe-Ito's #F0E442 vanishes on white. */
export const PALETTE = [
  "#0072b2", // blue
  "#e69f00", // orange
  "#009e73", // bluish green
  "#d55e00", // vermillion
  "#56b4e9", // sky blue
  "#cc79a7", // reddish purple
  "#b0a000", // yellow, darkened for white backgrounds
  "#6b7280", // grey
];

function arc(cx: number, cy: number, r: number, from: number, to: number): string {
  // Angles in turns, clockwise from 12 o'clock.
  const a0 = (from - 0.25) * 2 * Math.PI;
  const a1 = (to - 0.25) * 2 * Math.PI;
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const large = to - from > 0.5 ? 1 : 0;
  return `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
}

export function PieChart({
  slices,
  scale,
  commodity,
  linkFor,
  locale,
}: {
  slices: readonly PieSlice[];
  scale: number;
  commodity: string;
  /** A register link for a slice that is one account. */
  linkFor?: (slice: PieSlice) => string | null;
  locale?: string | undefined;
}) {
  const t = useT();
  const [active, setActive] = useState<number | null>(null);
  if (slices.length === 0) return <p className="empty">{t("Nothing to show for this period.")}</p>;
  const total = slices.reduce((acc, s) => acc + s.minor, 0);
  let cursor = 0;
  const wedges = slices.map((s, i) => {
    const from = cursor;
    cursor += s.share;
    const to = i === slices.length - 1 ? 1 : cursor;
    return { slice: s, from, to, colour: PALETTE[i % PALETTE.length]! };
  });
  return (
    <div className="pie">
      <svg viewBox="0 0 200 200" className="pie__svg" role="img" aria-label={t("Pie chart")}>
        {wedges.length === 1 ? (
          <circle cx={100} cy={100} r={90} fill={wedges[0]!.colour}>
            <title>{`${wedges[0]!.slice.label}: ${formatMinor(wedges[0]!.slice.minor, scale, { locale })} ${commodity}`}</title>
          </circle>
        ) : (
          wedges.map((w, i) => (
            <path
              key={i}
              d={arc(100, 100, active === i ? 95 : 90, w.from, w.to)}
              fill={w.colour}
              stroke="#fff"
              strokeWidth={1}
              onMouseEnter={() => setActive(i)}
              onMouseLeave={() => setActive(null)}
            >
              <title>{`${w.slice.label}: ${formatMinor(w.slice.minor, scale, { locale })} ${commodity} (${(w.slice.share * 100).toFixed(1)}%)`}</title>
            </path>
          ))
        )}
      </svg>
      <table className="pie__legend report">
        <tbody>
          {wedges.map((w, i) => {
            const href = linkFor?.(w.slice) ?? null;
            return (
              <tr key={i} className={active === i ? "pie__legend-row--active" : undefined} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(null)}>
                <td>
                  <span className="pie__swatch" style={{ background: w.colour }} aria-hidden="true" />
                </td>
                <td>{href ? <Link to={href}>{w.slice.label}</Link> : w.slice.label}</td>
                <td className="num">{formatMinor(w.slice.minor, scale, { locale })}</td>
                <td className="num muted">{(w.slice.share * 100).toFixed(1)}%</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="report__total">
            <td />
            <th scope="row">{t("Total")}</th>
            <td className="num">{formatMinor(total, scale, { locale })}</td>
            <td className="num muted">{commodity}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** A round gridline spacing (1, 2, 5 times a power of ten) giving about four lines over `span`. */
export function niceStep(span: number): number {
  const raw = Math.max(span / 4, Number.EPSILON);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const n = raw / magnitude;
  return (n > 5 ? 10 : n > 2 ? 5 : n > 1 ? 2 : 1) * magnitude;
}

export interface LineSeries {
  name: string;
  /** Minor units per point, aligned with `labels`. */
  values: readonly number[];
  colour?: string;
}

export function LineChart({
  labels,
  series,
  scale,
  commodity,
  locale,
}: {
  labels: readonly string[];
  series: readonly LineSeries[];
  scale: number;
  commodity: string;
  locale?: string | undefined;
}) {
  const t = useT();
  if (labels.length === 0) return <p className="empty">{t("Nothing to show for this period.")}</p>;
  const width = 720;
  const height = 320;
  const pad = { left: 72, right: 16, top: 16, bottom: 40 };
  const all = series.flatMap((s) => s.values);
  let low = Math.min(0, ...all);
  let high = Math.max(0, ...all);
  if (low === high) high = low + scale;
  const margin = (high - low) * 0.05;
  low -= margin;
  high += margin;
  const x = (i: number) => pad.left + (labels.length === 1 ? (width - pad.left - pad.right) / 2 : (i * (width - pad.left - pad.right)) / (labels.length - 1));
  const y = (v: number) => pad.top + ((high - v) * (height - pad.top - pad.bottom)) / (high - low);
  // Five gridlines on round numbers of the major unit.
  const ticks: number[] = [];
  const step = niceStep((high - low) / scale);
  for (let v = Math.ceil(low / scale / step) * step; v * scale <= high; v += step) ticks.push(Math.round(v * scale));
  const every = Math.max(1, Math.ceil(labels.length / 12));
  return (
    <figure className="line">
      <svg viewBox={`0 0 ${width} ${height}`} className="line__svg" role="img" aria-label={t("Line chart")}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} stroke={v === 0 ? "#6b7280" : "#e5e7eb"} />
            <text x={pad.left - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="#6b7280">
              {formatMinor(v, scale, { locale })}
            </text>
          </g>
        ))}
        {labels.map((label, i) =>
          i % every === 0 || i === labels.length - 1 ? (
            <text key={label} x={x(i)} y={height - pad.bottom + 18} textAnchor="middle" fontSize={11} fill="#6b7280">
              {label}
            </text>
          ) : null,
        )}
        {series.map((s, si) => {
          const colour = s.colour ?? PALETTE[si % PALETTE.length]!;
          const d = s.values.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
          return (
            <g key={s.name}>
              <path d={d} fill="none" stroke={colour} strokeWidth={2} />
              {s.values.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={3} fill={colour}>
                  <title>{`${s.name} ${labels[i]}: ${formatMinor(v, scale, { locale })} ${commodity}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <figcaption className="line__legend">
        {series.map((s, si) => (
          <span key={s.name} className="line__legend-item">
            <span className="pie__swatch" style={{ background: s.colour ?? PALETTE[si % PALETTE.length] }} aria-hidden="true" /> {s.name}
          </span>
        ))}
      </figcaption>
      <table className="report line__table">
        <thead>
          <tr>
            <th scope="col">{t("Period")}</th>
            {series.map((s) => (
              <th key={s.name} scope="col" className="num">
                {s.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {labels.map((label, i) => (
            <tr key={label}>
              <td>{label}</td>
              {series.map((s) => (
                <td key={s.name} className="num">
                  {formatMinor(s.values[i] ?? 0, scale, { locale })}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

import { useT } from "@pymoney/core/hooks";
import { displayOwnValue, displayQuantity, hasChildren, leafName, visibleRows } from "@pymoney/core";
import type { AccountClass, Amount, ReportRow, Section } from "@pymoney/core";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Money } from "./Money";

/**
 * A hierarchical report: one section per account class, rows indented by
 * depth, parents collapsible. Shows `display_value` (signed to read naturally:
 * debt and income positive) and, where it differs, the account's own value.
 */
export function ReportTable({
  sections,
  totals,
  valueHeader = "Balance",
  linkFor,
  emptyMessage,
  reportCurrency,
}: {
  sections: Section[];
  /** Rows appended at the bottom, e.g. net worth. */
  totals?: { label: string; amount: Amount | null | undefined; emphasis?: boolean }[];
  valueHeader?: string;
  /** Where a row's name leads: typically the account's register for the same window. */
  linkFor?: (row: ReportRow) => string;
  emptyMessage?: string | undefined;
  reportCurrency?: string | undefined;
}) {
  const t = useT();
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (sections.length === 0) {
    return <p className="empty">{emptyMessage ?? t("Nothing to show for these settings.")}</p>;
  }

  return (
    <table className="report">
      <thead>
        <tr>
          <th scope="col">{t("Account")}</th>
          <th scope="col" className="num">
            {t("Own")}
          </th>
          <th scope="col" className="num">
            {t("Quantity")}
          </th>
          <th scope="col" className="num">
            {valueHeader}
          </th>
        </tr>
      </thead>
      {sections.map((section) => (
        <tbody key={section.accountClass}>
          {/* One root row (the usual "Assets" under Assets) is its own header. */}
          {section.rows.filter((r) => r.depth === 0).length !== 1 && (
            <tr className="report__section">
              <th scope="rowgroup" colSpan={2}>
                {t(section.label)}
              </th>
              <td className="num">
                <Money amount={section.total} strong />
              </td>
            </tr>
          )}
          {visibleRows(section.rows, collapsed).map((row) => (
            <Row
              key={row.account_id}
              row={row}
              accountClass={section.accountClass}
              collapsed={collapsed.has(row.account_id)}
              onToggle={toggle}
              href={linkFor?.(row)}
              currency={reportCurrency ?? ""}
            />
          ))}
        </tbody>
      ))}
      {totals && totals.length > 0 && (
        <tfoot>
          {totals.map((row) => (
            <tr key={row.label} className={row.emphasis ? "report__total report__total--emphasis" : "report__total"}>
              <th scope="row" colSpan={3}>
                {row.label}
              </th>
              <td className="num">
                <Money amount={row.amount} strong={row.emphasis ?? false} withCommodity />
              </td>
            </tr>
          ))}
        </tfoot>
      )}
    </table>
  );
}

function Row({
  row,
  accountClass,
  collapsed,
  onToggle,
  href,
  currency,
}: {
  row: ReportRow;
  accountClass: AccountClass;
  collapsed: boolean;
  onToggle: (id: string) => void;
  href?: string | undefined;
  currency: string;
}) {
  const t = useT();
  const parent = hasChildren(row);
  return (
    <tr className={`report__row depth-${Math.min(row.depth, 6)}${parent ? " report__row--parent" : ""}`}>
      <td>
        <span className="indent" style={{ ["--depth" as string]: row.depth }}>
          {parent ? (
            <button
              type="button"
              className="disclosure"
              aria-expanded={!collapsed}
              aria-label={`${collapsed ? t("Expand") : t("Collapse")} ${row.path}`}
              onClick={() => onToggle(row.account_id)}
            >
              {collapsed ? "▸" : "▾"}
            </button>
          ) : (
            <span className="disclosure disclosure--leaf" aria-hidden="true" />
          )}
          {href ? (
            <Link to={href} title={`${row.path} — open the register`} className="account-link">
              {leafName(row.path)}
            </Link>
          ) : (
            <span title={row.path}>{leafName(row.path)}</span>
          )}
        </span>
      </td>
      <td className="num muted">
        {parent ? <Money amount={displayOwnValue(row, accountClass)} blankZero /> : null}
      </td>
      <td className="num muted">
        <Money amount={displayQuantity(row, accountClass, currency)} blankZero withCommodity />
      </td>
      <td className="num">
        <Money amount={row.display_value} strong={row.depth === 0} />
      </td>
    </tr>
  );
}

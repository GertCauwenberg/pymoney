import { useT } from "@pymoney/core/hooks";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";

/** The graphical reports share one tab and a row of sub-tabs. */
export function ChartsShell() {
  const t = useT();
  // Hand the book context down: a nested Outlet does not inherit its parent's.
  const context = useOutletContext();
  return (
    <>
      <nav className="tabs tabs--sub" aria-label={t("Charts")}>
        <NavLink to="income">{t("Income by category")}</NavLink>
        <NavLink to="expenses">{t("Expenses by category")}</NavLink>
        <NavLink to="net-worth">{t("Net worth over time")}</NavLink>
        <NavLink to="by-period">{t("Income and expenses by period")}</NavLink>
        <NavLink to="holdings">{t("Holdings by position")}</NavLink>
      </nav>
      <Outlet context={context} />
    </>
  );
}

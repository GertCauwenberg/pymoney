import { useT } from "@pymoney/core/hooks";
import { NavLink, Outlet, useOutletContext } from "react-router-dom";

import type { BookContext } from "./BookShell";

/** The book's reference data and sharing, behind one tab with sub-tabs. */
export function SettingsShell() {
  const t = useT();
  // Hand the book context down: a nested Outlet does not inherit its parent's.
  const context = useOutletContext<BookContext>();
  return (
    <>
      <nav className="tabs tabs--sub" aria-label={t("Settings")}>
        <NavLink to="accounts">{t("Accounts")}</NavLink>
        <NavLink to="payees">{t("Payees")}</NavLink>
        <NavLink to="commodities">{t("Commodities")}</NavLink>
        <NavLink to="prices">{t("Prices")}</NavLink>
        {context.role === "owner" && <NavLink to="sharing">{t("Sharing")}</NavLink>}
      </nav>
      <Outlet context={context} />
    </>
  );
}

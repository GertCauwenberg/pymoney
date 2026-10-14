import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { RequireAuth } from "./components/RequireAuth";
import { AccountsPage } from "./pages/AccountsPage";
import { BookShell } from "./pages/BookShell";
import { CommoditiesPage } from "./pages/CommoditiesPage";
import { PricesPage } from "./pages/PricesPage";
import { BooksPage } from "./pages/BooksPage";
import { LoginPage } from "./pages/LoginPage";
import { ActivatePage, ForgotPasswordPage, ResetPasswordPage } from "./pages/PasswordPages";
import { NotFound } from "./pages/NotFound";
import { HistoryPage } from "./pages/HistoryPage";
import { PayeesPage } from "./pages/PayeesPage";
import { SchedulesPage } from "./pages/SchedulesPage";
import { SettingsShell } from "./pages/SettingsShell";
import { SharingPage } from "./pages/SharingPage";
import { DeletingPage } from "./pages/DeletePage";
import { BalanceSheetReport } from "./reports/BalanceSheetReport";
import { CategoryPieReport } from "./reports/CategoryPieReport";
import { ChartsShell } from "./reports/ChartsShell";
import { HoldingsPieReport } from "./reports/HoldingsPieReport";
import { NetWorthReport } from "./reports/NetWorthReport";
import { PeriodTableReport } from "./reports/PeriodTableReport";
import { BudgetReport } from "./reports/BudgetReport";
import { HoldingsReport } from "./reports/HoldingsReport";
import { IncomeStatementReport } from "./reports/IncomeStatementReport";
import { AccountRegisterPage } from "./ledger/AccountRegisterPage";
import { ImportStatementPage } from "./ledger/ImportStatementPage";
import { SearchPage } from "./ledger/SearchPage";
import { TransactionPage } from "./ledger/TransactionPage";

/** A redirect that carries the query string along (`accounts?edit=…`). */
function RedirectKeepingSearch({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/activate" element={<ActivatePage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Navigate to="/books" replace />} />
        <Route path="/books" element={<BooksPage />} />
        <Route path="/books/:bookId" element={<BookShell />}>
          <Route index element={<Navigate to="reports/balance-sheet" replace />} />
          <Route path="reports" element={<Navigate to="balance-sheet" replace />} />
          <Route path="reports/balance-sheet" element={<BalanceSheetReport />} />
          <Route path="reports/income-statement" element={<IncomeStatementReport />} />
          <Route path="reports/holdings" element={<HoldingsReport />} />
          <Route path="reports/charts" element={<ChartsShell />}>
            <Route index element={<Navigate to="expenses" replace />} />
            <Route path="income" element={<CategoryPieReport kind="INCOME" />} />
            <Route path="expenses" element={<CategoryPieReport kind="EXPENSE" />} />
            <Route path="net-worth" element={<NetWorthReport />} />
            <Route path="by-period" element={<PeriodTableReport />} />
            <Route path="budget" element={<BudgetReport />} />
            <Route path="holdings" element={<HoldingsPieReport />} />
          </Route>
          <Route path="settings" element={<SettingsShell />}>
            <Route index element={<Navigate to="accounts" replace />} />
            <Route path="accounts" element={<AccountsPage />} />
            <Route path="payees" element={<PayeesPage />} />
            <Route path="schedules" element={<SchedulesPage />} />
            <Route path="commodities" element={<CommoditiesPage />} />
            <Route path="prices" element={<PricesPage />} />
            <Route path="history" element={<HistoryPage />} />
          </Route>
          <Route path="sharing" element={<SharingPage />} />
          <Route path="delete" element={<DeletingPage />} />
          {/* The old addresses keep working: bookmarks and links from before the Settings tab. */}
          <Route path="accounts" element={<RedirectKeepingSearch to="../settings/accounts" />} />
          <Route path="commodities" element={<Navigate to="../settings/commodities" replace />} />
          <Route path="prices" element={<Navigate to="../settings/prices" replace />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="accounts/:accountId" element={<AccountRegisterPage />} />
          <Route path="accounts/:accountId/import" element={<ImportStatementPage />} />
          <Route path="transactions/:transactionId" element={<TransactionPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

import { Navigate, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./components/RequireAuth";
import { BookShell } from "./pages/BookShell";
import { BooksPage } from "./pages/BooksPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFound } from "./pages/NotFound";
import { BalanceSheetReport } from "./reports/BalanceSheetReport";
import { HoldingsReport } from "./reports/HoldingsReport";
import { IncomeStatementReport } from "./reports/IncomeStatementReport";
import { AccountRegisterPage } from "./ledger/AccountRegisterPage";
import { TransactionPage } from "./ledger/TransactionPage";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<Navigate to="/books" replace />} />
        <Route path="/books" element={<BooksPage />} />
        <Route path="/books/:bookId" element={<BookShell />}>
          <Route index element={<Navigate to="reports/balance-sheet" replace />} />
          <Route path="reports" element={<Navigate to="balance-sheet" replace />} />
          <Route path="reports/balance-sheet" element={<BalanceSheetReport />} />
          <Route path="reports/income-statement" element={<IncomeStatementReport />} />
          <Route path="reports/holdings" element={<HoldingsReport />} />
          <Route path="accounts/:accountId" element={<AccountRegisterPage />} />
          <Route path="transactions/:transactionId" element={<TransactionPage />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

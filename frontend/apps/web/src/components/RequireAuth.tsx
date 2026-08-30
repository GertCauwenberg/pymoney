import { useT } from "@pymoney/core/hooks";
import { useSession } from "@pymoney/core/hooks";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ErrorBanner } from "./ErrorBanner";
import { Loading } from "./Loading";

/** Gate: renders children for a logged-in user, otherwise sends to /login. */
export function RequireAuth() {
  const t = useT();
  const session = useSession();
  const location = useLocation();

  if (session.isPending) return <Loading label={t("Checking your session…")} />;
  if (session.isError) {
    return (
      <main className="page page--narrow">
        <ErrorBanner error={session.error} title={t("Cannot reach the server")} onRetry={() => void session.refetch()} />
      </main>
    );
  }
  if (session.data === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

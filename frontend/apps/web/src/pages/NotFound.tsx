import { useT } from "@pymoney/core/hooks";
import { Link } from "react-router-dom";

export function NotFound() {
  const t = useT();
  return (
    <main className="page page--narrow">
      <h1>{t("Not found")}</h1>
      <p>
        {t("There is nothing at this address.")} <Link to="/">{t("Start again")}</Link>.
      </p>
    </main>
  );
}

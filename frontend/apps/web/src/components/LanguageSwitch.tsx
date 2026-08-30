import { LOCALES, type Locale } from "@pymoney/core";
import { useI18n, useSession, useUpdateMe } from "@pymoney/core/hooks";

/**
 * Pick a language. Before login it is remembered in this browser; once logged
 * in it is stored with the account, so every client of this user follows.
 */
export function LanguageSwitch() {
  const { locale, setLocale, t } = useI18n();
  const session = useSession();
  const update = useUpdateMe();
  return (
    <select
      className="language"
      aria-label={t("Language")}
      value={locale}
      onChange={(e) => {
        const next = e.target.value as Locale;
        setLocale(next);
        if (session.data) update.mutate({ locale: next });
      }}
    >
      {LOCALES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.name}
        </option>
      ))}
    </select>
  );
}

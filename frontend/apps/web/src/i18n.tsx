import { normaliseLocale, type Locale } from "@pymoney/core";
import { I18nProvider, useI18n, useSession } from "@pymoney/core/hooks";
import { useCallback, useEffect, useState, type ReactNode } from "react";

const KEY = "pymoney.locale";

function initialLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(KEY);
    if (stored) return normaliseLocale(stored);
  } catch {
    // no storage: fall through to the browser's language
  }
  return normaliseLocale(typeof navigator === "undefined" ? null : navigator.language);
}

/** Holds the locale for the whole app: this browser's choice, then the user's. */
export function AppI18n({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // fine without
    }
    document.documentElement.lang = next;
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  return (
    <I18nProvider locale={locale} onChange={setLocale}>
      <FollowTheUser />
      {children}
    </I18nProvider>
  );
}

/** The account's stored language wins over the browser's once we know it. */
function FollowTheUser() {
  const session = useSession();
  const { locale, setLocale } = useI18n();
  const stored = session.data?.locale;
  useEffect(() => {
    if (stored && normaliseLocale(stored) !== locale) setLocale(normaliseLocale(stored));
    // Only when the *user* changes (login, another account), not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.data?.id]);
  return null;
}

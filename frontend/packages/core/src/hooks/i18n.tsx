import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { DEFAULT_LOCALE, translator, type Locale, type Translate } from "../i18n";

interface I18n {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: Translate;
}

const I18nContext = createContext<I18n>({ locale: DEFAULT_LOCALE, setLocale: () => undefined, t: translator(DEFAULT_LOCALE) });

/** Provides the current locale and a translator; the app decides where the locale is kept. */
export function I18nProvider({
  locale,
  onChange,
  children,
}: {
  locale: Locale;
  onChange: (locale: Locale) => void;
  children: ReactNode;
}) {
  const setLocale = useCallback((next: Locale) => onChange(next), [onChange]);
  const value = useMemo<I18n>(() => ({ locale, setLocale, t: translator(locale) }), [locale, setLocale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  return useContext(I18nContext);
}

/** The translator for the current locale. */
export function useT(): Translate {
  return useContext(I18nContext).t;
}

export function useLocale(): Locale {
  return useContext(I18nContext).locale;
}

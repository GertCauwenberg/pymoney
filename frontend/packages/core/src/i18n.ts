/**
 * Translations. Keys are the English source strings; `nl.json` is generated
 * from the server's catalogue (server/src/pymoney/i18n/nl.json) by scripts/i18n.py,
 * so the web app, the desktop and the server's emails share one dictionary.
 *
 * `translate("Delete {name}?", "nl", { name })` substitutes `{name}` after
 * looking the key up, so a translator can move the placeholder.
 */
import nl from "./generated/nl.json";

export type Locale = "en" | "nl";
export const LOCALES: readonly { code: Locale; name: string }[] = [
  { code: "en", name: "English" },
  { code: "nl", name: "Nederlands" },
];
export const DEFAULT_LOCALE: Locale = "en";

const catalogues: Record<Locale, Record<string, string>> = { en: {}, nl: nl as Record<string, string> };

export function normaliseLocale(value: string | null | undefined): Locale {
  const short = (value ?? "").trim().toLowerCase().replace("_", "-").split("-")[0];
  return LOCALES.some((l) => l.code === short) ? (short as Locale) : DEFAULT_LOCALE;
}

export type Params = Record<string, string | number>;

export function translate(key: string, locale: Locale, params?: Params): string {
  let text = catalogues[locale][key] ?? key;
  if (params) for (const [name, value] of Object.entries(params)) text = text.split(`{${name}}`).join(String(value));
  return text;
}

/** A translator bound to one locale. */
export type Translate = (key: string, params?: Params) => string;

export function translator(locale: Locale): Translate {
  return (key, params) => translate(key, locale, params);
}

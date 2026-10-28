/**
 * UI strings, English first with a Bahasa Malaysia dictionary for key chrome.
 *
 * Scope note, stated plainly: this is a **client-side toggle over key UI strings**, not full
 * localisation. Page bodies, chart annotations and every generated sentence from the pipeline
 * remain English. Proper localisation means per-locale routes so each language is a real URL
 * that search engines and screen readers can reach — that is the next step, and
 * `docs/KNOWN-GAPS.md` says so.
 *
 * What is here is the structure: a single dictionary, a `t()` helper, and elements tagged
 * with `data-i18n` so adding a language is a data change rather than a markup change.
 *
 * Domain terms stay untranslated on purpose: "MCOICOP", "CPI", "HIES" and the DOSM state
 * names are proper nouns, and inventing Malay equivalents would make the site harder to
 * match against the source data, not easier.
 */

export const languages = {
  en: "English",
  bm: "Bahasa Malaysia",
} as const;

export type Lang = keyof typeof languages;

export const defaultLang: Lang = "en";

export const ui = {
  en: {
    "site.name": "Malaysia Cost-of-Living Reality Check",
    "site.tagline": "Is purchasing power actually improving?",
    "nav.overview": "Overview",
    "nav.income": "Income",
    "nav.states": "States",
    "nav.calculator": "Your basket",
    "nav.forecast": "Forecast",
    "nav.prices": "Prices",
    "nav.methodology": "Methodology",
    "common.asOf": "Data as of",
    "common.source": "Source",
    "common.updated": "Updated",
    "common.lastUpdated": "Last updated",
    "common.caveats": "Caveats",
    "common.noData": "no data",
    "common.allStates": "All states",
    "common.state": "State",
    "common.year": "Year",
    "common.percent": "Percent",
    "common.skipToContent": "Skip to content",
    "theme.toggle": "Toggle dark mode",
  },
  bm: {
    "site.name": "Semakan Realiti Kos Sara Hidup Malaysia",
    "site.tagline": "Adakah kuasa beli benar-benar meningkat?",
    "nav.overview": "Gambaran",
    "nav.income": "Pendapatan",
    "nav.states": "Negeri",
    "nav.calculator": "Bakul anda",
    "nav.forecast": "Ramalan",
    "nav.prices": "Harga",
    "nav.methodology": "Metodologi",
    "common.asOf": "Data sehingga",
    "common.source": "Sumber",
    "common.updated": "Dikemas kini",
    "common.lastUpdated": "Kemas kini terakhir",
    "common.caveats": "Kaveat",
    "common.noData": "tiada data",
    "common.allStates": "Semua negeri",
    "common.state": "Negeri",
    "common.year": "Tahun",
    "common.percent": "Peratus",
    "common.skipToContent": "Langkau ke kandungan",
    "theme.toggle": "Tukar mod gelap",
  },
} as const;

export type UiKey = keyof (typeof ui)["en"];

/** Translate a key, falling back to English and then to the key itself. */
export function t(lang: Lang, key: UiKey): string {
  return ui[lang]?.[key] ?? ui[defaultLang][key] ?? key;
}

export function isLang(value: string | null | undefined): value is Lang {
  return value === "en" || value === "bm";
}

import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import fr from "./locales/fr.json";
import ar from "./locales/ar.json";

// RTL languages: when active, flip the <html dir> attribute so the whole
// layout mirrors (logical CSS properties handle spacing automatically).
const RTL_LANGS = ["ar"];

function applyDirection(lang: string) {
  document.documentElement.dir = RTL_LANGS.includes(lang) ? "rtl" : "ltr";
  document.documentElement.lang = lang;
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      fr: { translation: fr },
      ar: { translation: ar },
    },
    fallbackLng: {
      default: ["fr"],
    },
    supportedLngs: ["fr", "ar"],
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
  });

// Keep <html dir> in sync on every language change
i18n.on("languageChanged", applyDirection);
applyDirection(i18n.language);

export default i18n;

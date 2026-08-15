import { useTranslation } from "react-i18next";
import { useUIStore } from "../../store/uiStore";

const LANGUAGES = [
  { code: "fr", label: "FR" },
  { code: "ar", label: "عربي" },
  { code: "derja", label: "دارجة" },
] as const;

export default function LanguageSwitcher() {
  const { language, setLanguage } = useUIStore();
  const { t } = useTranslation();

  return (
    <div className="flex items-center rounded-lg border border-border bg-background p-0.5">
      {LANGUAGES.map((lang) => (
        <button
          key={lang.code}
          onClick={() => setLanguage(lang.code)}
          className={`rounded-md px-2 py-1 text-xs font-semibold transition-colors ${
            language === lang.code
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
          aria-label={t("common.language")}
        >
          {lang.label}
        </button>
      ))}
    </div>
  );
}

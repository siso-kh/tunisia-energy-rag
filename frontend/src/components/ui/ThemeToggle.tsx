import { Moon, Sun } from "lucide-react";
import { useUIStore } from "../../store/uiStore";
import { useTranslation } from "react-i18next";

export default function ThemeToggle() {
  const { theme, toggleTheme } = useUIStore();
  const { t } = useTranslation();

  return (
    <button
      onClick={toggleTheme}
      className="flex size-8 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      aria-label={theme === "dark" ? t("common.light") : t("common.dark")}
      title={theme === "dark" ? t("common.light") : t("common.dark")}
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

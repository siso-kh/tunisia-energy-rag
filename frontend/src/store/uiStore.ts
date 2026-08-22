import { create } from "zustand";
import { persist } from "zustand/middleware";
import i18n from "../i18n";

export type Theme = "dark" | "light";
export type Language = "fr" | "ar";
export type SidebarTab = "carte" | "telemetrie" | "calculator" | "history";

interface UIState {
  theme: Theme;
  language: Language;
  sidebarTab: SidebarTab;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
  setLanguage: (language: Language) => void;
  setSidebarTab: (tab: SidebarTab) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set, get) => ({
      theme: "light",
      language: "fr",
      sidebarTab: "carte",
      setTheme: (theme) => {
        document.documentElement.classList.toggle("dark", theme === "dark");
        set({ theme });
      },
      toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
      setLanguage: (language) => {
        // Single source of truth: i18n.changeLanguage fires the
        // languageChanged handler which sets <html dir/lang>.
        void i18n.changeLanguage(language);
        set({ language });
      },
      setSidebarTab: (tab) => set({ sidebarTab: tab }),
    }),
    { name: "tunisia-energy-ui" }
  )
);

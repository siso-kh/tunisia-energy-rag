import { useEffect } from "react";
import i18n from "./i18n";
import { useUIStore } from "./store/uiStore";
import SiteHeader from "./components/layout/SiteHeader";
import ChatPanel from "./components/chat/ChatPanel";
import EnergySidebar from "./components/layout/EnergySidebar";

export default function App() {
  const theme = useUIStore((s) => s.theme);

  // Apply persisted theme + language on mount. The store persists under its
  // own localStorage key while i18n detects separately, so re-sync on boot.
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    const storedLanguage = useUIStore.getState().language;
    if (storedLanguage && i18n.language !== storedLanguage) {
      void i18n.changeLanguage(storedLanguage);
    }
  }, [theme]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <SiteHeader />

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 md:grid-cols-[1.5fr_1fr]">
        {/* Left: primary RAG chat workspace (60%) */}
        <div className="flex min-h-0 flex-col">
          <ChatPanel />
        </div>

        {/* Right: contextual energy sidebar (40%) */}
        <div className="hidden min-h-0 flex-col md:flex">
          <EnergySidebar />
        </div>
      </main>
    </div>
  );
}

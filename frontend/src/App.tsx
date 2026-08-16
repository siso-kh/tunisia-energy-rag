import { useEffect, useState } from "react";
import i18n from "./i18n";
import { useUIStore } from "./store/uiStore";
import { useAuthStore } from "./store/authStore";
import SiteHeader from "./components/layout/SiteHeader";
import ChatPanel from "./components/chat/ChatPanel";
import EnergySidebar from "./components/layout/EnergySidebar";
import AdminPage from "./pages/AdminPage";

// Minimal client-side routing: the admin lives on its own path so it is not
// surfaced in the public dashboard UI.
function usePathname() {
  const [pathname, setPathname] = useState(window.location.pathname);
  useEffect(() => {
    const onNavigate = () => setPathname(window.location.pathname);
    window.addEventListener("popstate", onNavigate);
    return () => window.removeEventListener("popstate", onNavigate);
  }, []);
  return pathname;
}

export default function App() {
  const theme = useUIStore((s) => s.theme);
  const pathname = usePathname();

  // Restore the session from the stored token (if any) on boot.
  useEffect(() => {
    void useAuthStore.getState().init();
  }, []);

  // Apply persisted theme + language on mount. The store persists under its
  // own localStorage key while i18n detects separately, so re-sync on boot.
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    const storedLanguage = useUIStore.getState().language;
    if (storedLanguage && i18n.language !== storedLanguage) {
      void i18n.changeLanguage(storedLanguage);
    }
  }, [theme]);

  if (pathname.startsWith("/admin")) {
    return <AdminPage />;
  }

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

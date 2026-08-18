import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BarChart3, FileSearch, ShieldCheck } from "lucide-react";
import AdminTab from "../components/sidebar/AdminTab";
import ConfigEditor from "../components/admin/ConfigEditor";
import DashboardTab from "../components/admin/DashboardTab";
import DocumentUpload from "../components/admin/DocumentUpload";
import SourcesManager from "../components/admin/SourcesManager";

type AdminView = "sources" | "dashboard";

export default function AdminPage() {
  const { t } = useTranslation();
  const [unlocked, setUnlocked] = useState(false);
  const [view, setView] = useState<AdminView>("sources");

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Admin header */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4 md:px-6">
        <div className="flex items-center gap-2.5">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <ShieldCheck className="size-4" aria-hidden="true" />
          </span>
          <div className="leading-tight">
            <span className="block text-sm font-bold tracking-tight text-foreground">
              {t("admin.pageTitle")}
            </span>
            <span className="block text-[10px] font-medium text-muted-foreground">
              {t("admin.pageSubtitle")}
            </span>
          </div>
        </div>

        <a
          href="/"
          className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <ArrowLeft className="size-3.5" aria-hidden="true" />
          {t("admin.backToDashboard")}
        </a>
      </header>

      {/* Admin content */}
      <main className="chat-scroll min-h-0 flex-1 overflow-y-auto bg-muted/30 p-4 md:p-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {/* Login gate + purge stats */}
          <AdminTab onUnlocked={() => setUnlocked(true)} />

          {unlocked && (
            <>
              {/* Config + single doc upload */}
              <ConfigEditor />
              <DocumentUpload />

              {/* Tab bar: Sources vs Dashboard */}
              <div className="flex gap-1 rounded-lg bg-muted p-1">
                {(
                  [
                    ["sources", "admin.tabs.sources", FileSearch],
                    ["dashboard", "admin.tabs.dashboard", BarChart3],
                  ] as [AdminView, string, typeof FileSearch][]
                ).map(([value, labelKey, Icon]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setView(value)}
                    aria-pressed={view === value}
                    className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium transition-colors ${
                      view === value
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <Icon className="size-3.5" aria-hidden="true" />
                    {t(labelKey)}
                  </button>
                ))}
              </div>

              {/* Active view */}
              {view === "sources" ? <SourcesManager /> : <DashboardTab />}
            </>
          )}
        </div>
      </main>

      {/* Footer */}
      <footer className="flex h-10 shrink-0 items-center justify-center border-t border-border bg-card px-4 text-[10px] text-muted-foreground">
        <span>
          Tunisia Energy RAG — {new Date().getFullYear()}
        </span>
      </footer>
    </div>
  );
}

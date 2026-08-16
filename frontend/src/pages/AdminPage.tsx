import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import AdminTab from "../components/sidebar/AdminTab";
import ConfigEditor from "../components/admin/ConfigEditor";

export default function AdminPage() {
  const { t } = useTranslation();
  // Config editor appears only after the login gate in AdminTab succeeds.
  const [unlocked, setUnlocked] = useState(false);

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
          {/* Login gate + purge stats (AdminTab handles both states) */}
          <AdminTab onUnlocked={() => setUnlocked(true)} />

          {/* Config editor only after unlock */}
          {unlocked && <ConfigEditor />}
        </div>
      </main>
    </div>
  );
}

import { useEffect, useState } from "react";
import { ArrowRight, LogOut, Shield, User, Zap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useUIStore } from "../../store/uiStore";
import { useAuthStore } from "../../store/authStore";
import LanguageSwitcher from "../ui/LanguageSwitcher";
import ThemeToggle from "../ui/ThemeToggle";
import Button from "../ui/Button";
import AuthModal from "../auth/AuthModal";

type NavKey = "chat" | "carte" | "calculator";

/**
 * Polls the backend /health endpoint so the status badge reflects the real
 * backend state (falls back to "unknown" if the backend is unreachable).
 */
function useBackendHealth() {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const res = await fetch("/health", { signal: AbortSignal.timeout(5000) });
        if (!cancelled) setHealthy(res.ok);
      } catch {
        if (!cancelled) setHealthy(false);
      }
    };
    check();
    const id = setInterval(check, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  return healthy;
}

export default function SiteHeader() {
  const { t } = useTranslation();
  const { setSidebarTab } = useUIStore();
  const { user, status, logout } = useAuthStore();
  const [active, setActive] = useState<NavKey>("chat");
  const [authOpen, setAuthOpen] = useState(false);
  const healthy = useBackendHealth();

  const NAV: { key: NavKey; label: string; onClick: () => void }[] = [
    {
      key: "chat",
      label: t("nav.chat"),
      onClick: () => setActive("chat"),
    },
    {
      key: "carte",
      label: t("nav.map"),
      onClick: () => {
        setActive("carte");
        setSidebarTab("carte");
      },
    },
    {
      key: "calculator",
      label: t("nav.calculator"),
      onClick: () => {
        setActive("calculator");
        setSidebarTab("calculator");
      },
    },
  ];

  const statusOk = healthy !== false;

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-card px-4 md:px-6">
      {/* Left: logo + brand */}
      <div className="flex items-center gap-2.5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Zap className="size-4" aria-hidden="true" />
        </span>
        <div className="hidden leading-tight sm:block">
          <span className="block text-sm font-bold tracking-tight text-foreground">
            {t("app.name")}
          </span>
          <span className="block text-[10px] font-medium text-muted-foreground">
            {t("app.tagline")}
          </span>
        </div>
      </div>

      {/* Center: nav */}
      <nav className="hidden items-center gap-1 md:flex">
        {NAV.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={item.onClick}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              active === item.key
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {/* Right: language + status + CTA */}
      <div className="flex items-center gap-2.5">
        {/* Discreet admin entry point -> separate /admin route (key-protected) */}
        <a
          href="/admin"
          aria-label={t("header.adminLink")}
          title={t("header.adminLink")}
          className="flex size-8 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Shield className="size-4" aria-hidden="true" />
        </a>

        <LanguageSwitcher />
        <ThemeToggle />

        {/* User auth: login button when anonymous, user chip + logout when authed */}
        {status === "authenticated" && user ? (
          <div className="flex items-center gap-1.5 rounded-lg border border-border bg-background px-2 py-1">
            <span className="flex size-5 items-center justify-center rounded-md bg-primary/10 text-primary">
              <User className="size-3" aria-hidden="true" />
            </span>
            <span className="max-w-28 truncate text-xs font-medium text-foreground" title={user.email ?? undefined}>
              {user.display_name || user.email}
            </span>
            <button
              type="button"
              onClick={logout}
              aria-label={t("header.logout")}
              title={t("header.logout")}
              className="flex size-5 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <LogOut className="size-3" aria-hidden="true" />
            </button>
          </div>
        ) : (
          <Button variant="outline" size="sm" onClick={() => setAuthOpen(true)}>
            {t("header.login")}
          </Button>
        )}

        <span
          className={`hidden items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium xl:flex ${
            statusOk ? "text-foreground" : "text-destructive"
          }`}
          title={statusOk ? t("header.statusOk") : t("header.statusDown")}
        >
          <span className="relative flex size-2">
            <span
              className={`absolute inline-flex size-full animate-ping rounded-full opacity-60 ${
                statusOk ? "bg-water" : "bg-destructive"
              }`}
            />
            <span
              className={`relative inline-flex size-2 rounded-full ${
                statusOk ? "bg-water" : "bg-destructive"
              }`}
            />
          </span>
          {statusOk ? t("header.statusOk") : t("header.statusDown")}
        </span>

        <Button
          size="sm"
          onClick={() => {
            setActive("carte");
            setSidebarTab("carte");
          }}
          aria-label={t("header.reportCta")}
        >
          <span className="hidden sm:inline">{t("header.reportCta")}</span>
          <span className="sm:hidden">{t("header.reportCtaShort")}</span>
          <ArrowRight className="size-4" aria-hidden="true" />
        </Button>
      </div>

      <AuthModal open={authOpen} onClose={() => setAuthOpen(false)} />
    </header>
  );
}

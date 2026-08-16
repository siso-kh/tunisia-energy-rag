import { FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { LogIn, UserPlus, X } from "lucide-react";
import Button from "../ui/Button";
import { useAuthStore } from "../../store/authStore";

type Mode = "login" | "register";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function AuthModal({ open, onClose }: Props) {
  const { t } = useTranslation();
  const { login, register, error, status, clearError } = useAuthStore();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");

  if (!open) return null;

  const switchMode = (next: Mode) => {
    setMode(next);
    clearError();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password, displayName || undefined);
      }
      setEmail("");
      setPassword("");
      setDisplayName("");
      onClose();
    } catch {
      /* error surfaced from the store */
    }
  };

  const busy = status === "loading";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t("auth.title")}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">{t("auth.title")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("auth.close")}
            className="flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        {/* Tabs */}
        <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg border border-border bg-background p-1">
          <button
            type="button"
            onClick={() => switchMode("login")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "login"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t("auth.loginTab")}
          </button>
          <button
            type="button"
            onClick={() => switchMode("register")}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === "register"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t("auth.registerTab")}
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          {mode === "register" && (
            <div>
              <label
                htmlFor="auth-display-name"
                className="mb-1 block text-xs text-muted-foreground"
              >
                {t("auth.displayName")} <span className="opacity-60">({t("auth.optional")})</span>
              </label>
              <input
                id="auth-display-name"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                autoComplete="name"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
              />
            </div>
          )}

          <div>
            <label
              htmlFor="auth-email"
              className="mb-1 block text-xs text-muted-foreground"
            >
              {t("auth.email")}
            </label>
            <input
              id="auth-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              placeholder="user@example.com"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
            />
          </div>

          <div>
            <label
              htmlFor="auth-password"
              className="mb-1 block text-xs text-muted-foreground"
            >
              {t("auth.password")}
            </label>
            <input
              id="auth-password"
              type="password"
              required
              minLength={mode === "register" ? 8 : 1}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="••••••••"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
            />
            {mode === "register" && (
              <p className="mt-1 text-[10px] text-muted-foreground">{t("auth.passwordHint")}</p>
            )}
          </div>

          {error && (
            <p className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          )}

          <Button type="submit" disabled={busy} className="mt-1 w-full">
            {mode === "login" ? (
              <LogIn className="size-4" aria-hidden="true" />
            ) : (
              <UserPlus className="size-4" aria-hidden="true" />
            )}
            {busy
              ? t("common.loading")
              : mode === "login"
                ? t("auth.loginSubmit")
                : t("auth.registerSubmit")}
          </Button>
        </form>

        <p className="mt-3 text-center text-[10px] text-muted-foreground">
          {t("auth.anonymousNote")}
        </p>
      </div>
    </div>
  );
}

import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import Button from "../ui/Button";
import {
  clearAdminKey,
  fetchPurgeStats,
  getAdminKey,
  runPurgeNow,
  setAdminKey,
} from "../../services/admin";
import { formatPurgeDate } from "../../lib/admin-format";
import { AxiosError } from "axios";

interface Props {
  /** Called once the admin key is accepted (stats fetched successfully). */
  onUnlocked?: () => void;
}

export default function AdminTab({ onUnlocked }: Props) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [keyInput, setKeyInput] = useState("");

  // No key yet -> show the login gate. The key lives in memory only.
  const [key, setKey] = useState<string | null>(getAdminKey());

  const { data: stats, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "purge-stats"],
    queryFn: fetchPurgeStats,
    enabled: !!key,
    retry: false,
  });

  // Notify the parent page that the key was accepted.
  useEffect(() => {
    if (key && stats) onUnlocked?.();
  }, [key, stats, onUnlocked]);

  const purge = useMutation({
    mutationFn: runPurgeNow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "purge-stats"] });
    },
  });

  const handleLogin = (e: FormEvent) => {
    e.preventDefault();
    setAdminKey(keyInput);
    setKey(getAdminKey());
    // Re-fetch with the new key (the query is enabled once a key exists).
    void refetch();
  };

  const handleLogout = () => {
    clearAdminKey();
    setKey(null);
    setKeyInput("");
  };

  // ---- Login gate ---------------------------------------------------------
  if (!key) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-foreground">{t("admin.title")}</h3>
            <p className="text-xs text-muted-foreground">{t("admin.subtitle")}</p>
          </div>
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ShieldCheck className="size-4" aria-hidden="true" />
          </span>
        </div>

        <form
          onSubmit={handleLogin}
          className="rounded-xl border border-border bg-card p-4 shadow-sm"
        >
          <label
            htmlFor="admin-api-key"
            className="mb-1 block text-xs text-muted-foreground"
          >
            {t("admin.apiKeyLabel")}
          </label>
          <input
            id="admin-api-key"
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            required
            autoComplete="off"
            placeholder="••••••••"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
          />
          <Button type="submit" className="mt-3 w-full">
            <KeyRound className="size-4" aria-hidden="true" />
            {t("admin.unlock")}
          </Button>
        </form>
      </div>
    );
  }

  // ---- Stats view ---------------------------------------------------------
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("admin.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("admin.subtitle")}</p>
        </div>
        <span className="flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <ShieldCheck className="size-4" aria-hidden="true" />
        </span>
      </div>

      {isLoading ? (
        <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
      ) : isError ? (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">{t("admin.invalidKey")}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={handleLogout}>
            {t("admin.tryAgain")}
          </Button>
        </div>
      ) : (
        <>
          {/* TTL config */}
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border bg-card p-3">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("admin.ttl")}
              </p>
              <p className="mt-1 text-lg font-bold text-foreground">
                {stats?.ttl_hours ?? "—"}
                <span className="ms-1 text-xs font-medium text-muted-foreground">
                  {t("admin.hours")}
                </span>
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-3">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("admin.interval")}
              </p>
              <p className="mt-1 text-lg font-bold text-foreground">
                {stats?.purge_interval_minutes ?? "—"}
                <span className="ms-1 text-xs font-medium text-muted-foreground">
                  {t("admin.minutes")}
                </span>
              </p>
            </div>
          </div>

          {/* Lifetime totals */}
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-border bg-card p-3">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("admin.totalRuns")}
              </p>
              <p className="mt-1 text-lg font-bold text-foreground">
                {stats?.total_runs ?? 0}
              </p>
            </div>
            <div className="rounded-lg border border-border bg-card p-3">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {t("admin.totalDeleted")}
              </p>
              <p className="mt-1 text-lg font-bold text-foreground">
                {stats?.total_deleted ?? 0}
              </p>
            </div>
          </div>

          {/* Last / next run */}
          <div className="rounded-lg border border-border bg-card p-3 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">{t("admin.lastRun")}</span>
              <span className="font-medium text-foreground">
                {stats?.last_run_at ? formatPurgeDate(stats.last_run_at) : "—"}
              </span>
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-muted-foreground">{t("admin.nextRun")}</span>
              <span className="font-medium text-foreground">
                {stats?.next_run_at ? formatPurgeDate(stats.next_run_at) : "—"}
              </span>
            </div>
          </div>

          {/* Purge now */}
          <Button
            onClick={() => purge.mutate()}
            disabled={purge.isPending}
            className="w-full"
          >
            {purge.isPending ? (
              <RefreshCw className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Trash2 className="size-4" aria-hidden="true" />
            )}
            {purge.isPending ? t("admin.purging") : t("admin.purgeNow")}
          </Button>

          {purge.isError &&
            (purge.error as AxiosError | undefined)?.response?.status === 401 && (
              <p className="text-center text-xs text-destructive">
                {t("admin.invalidKey")}
              </p>
            )}
          {purge.isError &&
            (purge.error as AxiosError | undefined)?.response?.status !== 401 && (
              <p className="text-center text-xs text-destructive">{t("chat.error")}</p>
            )}
          {purge.data && (
            <p className="text-center text-xs text-green-600 dark:text-green-400">
              {t("admin.purged", { count: purge.data.deleted })}
            </p>
          )}

          {/* Recent runs */}
          <div>
            <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {t("admin.recentRuns")}
            </p>
            {stats && stats.recent_runs.length === 0 ? (
              <p className="text-xs text-muted-foreground">{t("admin.noRuns")}</p>
            ) : (
              <ul className="space-y-1.5">
                {stats?.recent_runs.map((run) => (
                  <li
                    key={run.run}
                    className="flex items-center justify-between gap-2 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs"
                  >
                    <span className="font-mono text-muted-foreground">
                      #{run.run}
                    </span>
                    <span className="flex-1 truncate text-muted-foreground">
                      {formatPurgeDate(run.at)}
                    </span>
                    <span
                      className={`font-semibold ${
                        run.error ? "text-destructive" : "text-foreground"
                      }`}
                    >
                      {run.error
                        ? t("admin.failed")
                        : `${run.deleted} ${t("admin.deletedShort")}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

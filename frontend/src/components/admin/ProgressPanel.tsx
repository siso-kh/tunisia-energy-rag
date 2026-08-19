import { useTranslation } from "react-i18next";
import type { ProgressEvent } from "../../services/admin";
import Spinner from "../ui/Spinner";

interface ProgressPanelProps {
  /** "research" or "ingest" — controls which labels/stats are shown. */
  mode: "research" | "ingest";
  /** The latest SSE progress event. */
  event: ProgressEvent | null;
  /** Whether the operation is still running. */
  running: boolean;
}

export default function ProgressPanel({ mode, event, running }: ProgressPanelProps) {
  const { t } = useTranslation();

  if (!running && !event) return null;

  const total = event?.total ?? 0;
  const current = event?.current ?? 0;
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  // Determine the current action label
  const actionLabel =
    event?.status === "downloading"
      ? t("admin.sources.researching")
      : event?.status === "ingesting"
        ? t("admin.sources.ingesting")
        : running
          ? mode === "research"
            ? t("admin.sources.researching")
            : t("admin.sources.ingesting")
          : t("admin.progress.done");

  // Stats to show depend on mode
  const stats: { label: string; value: number | string; color?: string }[] = [];

  if (mode === "research") {
    stats.push(
      { label: t("admin.progress.downloaded"), value: event?.downloaded ?? 0, color: "text-emerald-400" },
      { label: t("admin.progress.failed"), value: event?.failed ?? 0, color: "text-red-400" },
    );
  } else {
    stats.push(
      { label: t("admin.progress.indexed"), value: event?.indexed ?? 0, color: "text-emerald-400" },
      { label: t("admin.progress.rejected"), value: event?.rejected ?? 0, color: "text-amber-400" },
      { label: t("admin.progress.failed"), value: event?.failed ?? 0, color: "text-red-400" },
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2 text-sm font-medium">
        {running ? (
          <Spinner className="h-4 w-4 shrink-0" />
        ) : (
          <span className="text-emerald-400">✓</span>
        )}
        <span>{actionLabel}</span>
        {total > 0 && (
          <span className="ml-auto text-xs text-muted-foreground">
            {current}/{total} ({pct}%)
          </span>
        )}
      </div>

      {/* Progress bar */}
      {total > 0 && (
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full transition-all duration-300 ease-out ${running ? "bg-primary" : "bg-emerald-500"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}

      {/* Current file */}
      {event?.filename && running && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground truncate">
          <span className="shrink-0">📄</span>
          <span className="truncate font-mono">{event.filename}</span>
        </div>
      )}

      {/* Error on current file */}
      {event?.error && event?.type === "file_done" && (
        <div className="text-xs text-red-400 truncate" title={event.error}>
          ✗ {event.error}
        </div>
      )}

      {/* Stats row */}
      <div className="flex flex-wrap gap-3 text-xs">
        {stats.map((s) => (
          <div key={s.label} className="flex items-center gap-1">
            <span className="text-muted-foreground">{s.label}:</span>
            <span className={`font-semibold ${s.color ?? ""}`}>{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

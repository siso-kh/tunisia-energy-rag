import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Trash2,
  XCircle,
} from "lucide-react";
import Button from "../ui/Button";
import {
  bulkDeleteSources,
  deleteSource,
  fetchSources,
  Source,
  SourceStatus,
} from "../../services/admin";

type FilterStatus = "all" | "indexed" | "triage_rejected" | "failed";

const STATUS_STYLES: Record<SourceStatus, string> = {
  pending: "bg-muted text-muted-foreground",
  downloading: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  downloaded: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-destructive/10 text-destructive",
  ingesting: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  indexed: "bg-green-600 text-white dark:bg-green-500/80",
  triage_rejected: "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400",
};

function formatSize(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DashboardTab() {
  const { t } = useTranslation();
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [clearing, setClearing] = useState(false);

  const load = async () => {
    try {
      const all = await fetchSources();
      // Only show completed sources (not pending/downloaded).
      const completed = all.filter(
        (s) =>
          s.status === "indexed" ||
          s.status === "triage_rejected" ||
          s.status === "failed"
      );
      setSources(completed);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered =
    filter === "all"
      ? sources
      : sources.filter((s) => s.status === filter);

  const indexed = sources.filter((s) => s.status === "indexed");
  const rejected = sources.filter((s) => s.status === "triage_rejected");
  const failed = sources.filter((s) => s.status === "failed");
  const totalChunks = indexed.reduce((sum, s) => sum + (s.chunks_indexed || 0), 0);

  const handleDelete = async (id: string) => {
    try {
      await deleteSource(id);
      setSources((prev) => prev.filter((s) => s.id !== id));
    } catch (e) {
      console.error(e);
    }
  };

  const handleClearAll = async () => {
    if (!confirm(t("admin.dashboard.confirmClear"))) return;
    setClearing(true);
    try {
      const statusFilter =
        filter === "all" ? undefined : (filter as SourceStatus);
      await bulkDeleteSources({ status: statusFilter });
      await load();
    } catch (e) {
      console.error(e);
    } finally {
      setClearing(false);
    }
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted-foreground">
        {t("common.loading")}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">
            {t("admin.dashboard.title")}
          </h3>
          <p className="text-xs text-muted-foreground">
            {t("admin.dashboard.subtitle")}
          </p>
        </div>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-lg border border-border bg-background p-3 text-center">
          <CheckCircle2 className="mx-auto mb-1 size-4 text-green-600" />
          <p className="text-lg font-bold text-foreground">{indexed.length}</p>
          <p className="text-[10px] text-muted-foreground">
            {t("admin.dashboard.indexed")}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-background p-3 text-center">
          <AlertTriangle className="mx-auto mb-1 size-4 text-orange-500" />
          <p className="text-lg font-bold text-foreground">{rejected.length}</p>
          <p className="text-[10px] text-muted-foreground">
            {t("admin.dashboard.rejected")}
          </p>
        </div>
        <div className="rounded-lg border border-border bg-background p-3 text-center">
          <XCircle className="mx-auto mb-1 size-4 text-destructive" />
          <p className="text-lg font-bold text-foreground">{failed.length}</p>
          <p className="text-[10px] text-muted-foreground">
            {t("admin.dashboard.errors")}
          </p>
        </div>
      </div>

      {/* Total chunks */}
      <div className="rounded-lg border border-border bg-background px-3 py-2 text-xs text-muted-foreground">
        {t("admin.dashboard.totalChunks")}:{" "}
        <span className="font-bold text-foreground">{totalChunks.toLocaleString()}</span>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 rounded-lg bg-muted p-1">
        {(
          [
            ["all", "admin.dashboard.all"],
            ["indexed", "admin.dashboard.indexed"],
            ["triage_rejected", "admin.dashboard.rejected"],
            ["failed", "admin.dashboard.errors"],
          ] as [FilterStatus, string][]
        ).map(([value, labelKey]) => (
          <button
            key={value}
            type="button"
            onClick={() => setFilter(value)}
            aria-pressed={filter === value}
            className={`flex-1 rounded-md px-2 py-1 text-xs font-medium transition-colors ${
              filter === value
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t(labelKey)}
          </button>
        ))}
      </div>

      {/* Clear button */}
      {filtered.length > 0 && (
        <Button
          onClick={handleClearAll}
          disabled={clearing}
          variant="outline"
          size="sm"
        >
          {clearing ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className="size-3.5" />
          )}
          {t("admin.dashboard.clearAll")}
        </Button>
      )}

      {/* Sources table */}
      {filtered.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("admin.dashboard.noLogs")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.file")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.statusLabel")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.size")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.score")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.dashboard.gate1")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.chunks")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.dashboard.date")}</th>
                <th className="pb-1.5 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((src) => (
                <tr
                  key={src.id}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="max-w-[140px] truncate py-1.5 pr-2 font-mono text-foreground">
                    {src.filename ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${STATUS_STYLES[src.status]}`}
                    >
                      {t(`admin.sources.statusLabels.${src.status}`)}
                    </span>
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {formatSize(src.file_size)}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {src.master_score != null ? src.master_score.toFixed(1) : "—"}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {src.gate1_score != null ? src.gate1_score.toFixed(1) : "—"}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {src.chunks_indexed ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {new Date(src.updated_at).toLocaleDateString()}
                  </td>
                  <td className="py-1.5">
                    <div className="flex items-center gap-1">
                      <a
                        href={src.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        title={src.url}
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                      <button
                        onClick={() => handleDelete(src.id)}
                        className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                        title={t("admin.sources.delete")}
                      >
                        <Trash2 className="size-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Error details for failed sources */}
      {filter === "failed" && filtered.length > 0 && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <p className="mb-2 text-xs font-semibold text-destructive">
            {t("admin.dashboard.errorDetails")}
          </p>
          <ul className="space-y-1">
            {filtered.map((src) => (
              <li key={src.id} className="text-[11px] text-destructive/80">
                <span className="font-mono">{src.filename ?? src.url.split("/").pop()}</span>
                {" — "}
                {src.error_message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

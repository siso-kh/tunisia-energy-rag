import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ExternalLink,
  FileSearch,
  Globe,
  Loader2,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import Button from "../ui/Button";
import ProgressPanel from "./ProgressPanel";
import {
  addSource,
  crawlWebsite,
  deleteSource,
  fetchSources,
  ingestSourcesStream,
  ProgressEvent,
  researchSourcesStream,
  Source,
  SourceStatus,
} from "../../services/admin";

/** Badge colors per status. */
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

type InputMode = "url" | "crawl";

export default function SourcesManager() {
  const { t } = useTranslation();
  const [sources, setSources] = useState<Source[]>([]);
  const [inputMode, setInputMode] = useState<InputMode>("url");
  const [urlInput, setUrlInput] = useState("");
  const [crawlDepth, setCrawlDepth] = useState(1);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [researching, setResearching] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [researchResult, setResearchResult] = useState<string | null>(null);
  const [ingestResult, setIngestResult] = useState<string | null>(null);
  const [progressEvent, setProgressEvent] = useState<ProgressEvent | null>(null);
  const [progressMode, setProgressMode] = useState<"research" | "ingest">("research");

  const load = async () => {
    try {
      const all = await fetchSources();
      // Only show pending/downloaded sources (actionable items).
      // Completed sources (indexed/rejected/failed) are in the Dashboard tab.
      setSources(
        all.filter(
          (s) => s.status === "pending" || s.status === "downloaded"
        )
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = urlInput.trim();
    if (!trimmed) return;
    setAdding(true);
    setError(null);
    setResearchResult(null);
    setIngestResult(null);
    try {
      if (inputMode === "crawl") {
        const result = await crawlWebsite(trimmed, crawlDepth);
        setUrlInput("");
        setResearchResult(
          t("admin.sources.crawlDone", {
            found: result.found,
            added: result.added,
            skipped: result.skipped,
            pages: result.pages_crawled,
          })
        );
      } else {
        await addSource(trimmed);
        setUrlInput("");
      }
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteSource(id);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const handleResearch = async () => {
    setResearching(true);
    setError(null);
    setResearchResult(null);
    setProgressMode("research");
    setProgressEvent(null);
    try {
      const result = await researchSourcesStream((ev) => setProgressEvent(ev));
      setResearchResult(
        t("admin.sources.researchDone", {
          downloaded: result.downloaded ?? 0,
          failed: result.failed ?? 0,
          total: result.total ?? 0,
        })
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setResearching(false);
    }
  };

  const handleIngest = async () => {
    setIngesting(true);
    setError(null);
    setIngestResult(null);
    setProgressMode("ingest");
    setProgressEvent(null);
    try {
      const result = await ingestSourcesStream((ev) => setProgressEvent(ev));
      setIngestResult(
        t("admin.sources.ingestDone", {
          indexed: result.indexed ?? 0,
          rejected: result.rejected ?? 0,
          failed: result.failed ?? 0,
          total: result.total ?? 0,
        })
      );
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setIngesting(false);
    }
  };

  const pendingCount = sources.filter((s) => s.status === "pending").length;
  const downloadedCount = sources.filter((s) => s.status === "downloaded").length;
  const indexedCount = sources.filter((s) => s.status === "indexed").length;

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-sm">
      {/* Header */}
      <div>
        <h3 className="text-sm font-semibold text-foreground">
          {t("admin.sources.title")}
        </h3>
        <p className="text-xs text-muted-foreground">
          {t("admin.sources.subtitle")}
        </p>
      </div>

      {/* Input mode toggle */}
      <div className="flex gap-1 rounded-lg bg-muted p-1">
        {(
          [
            ["url", "admin.sources.singleUrl", Plus],
            ["crawl", "admin.sources.crawlWebsite", Globe],
          ] as [InputMode, string, typeof Plus][]
        ).map(([value, labelKey, Icon]) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setInputMode(value);
              setError(null);
              setResearchResult(null);
              setIngestResult(null);
            }}
            aria-pressed={inputMode === value}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              inputMode === value
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {t(labelKey)}
          </button>
        ))}
      </div>

      {/* URL input form */}
      <form onSubmit={handleAdd} className="flex flex-col gap-2">
        <div className="flex gap-2">
          <input
            type="url"
            value={urlInput}
            onChange={(e) => {
              setUrlInput(e.target.value);
              setError(null);
            }}
            placeholder={
              inputMode === "crawl"
                ? t("admin.sources.crawlPlaceholder")
                : t("admin.sources.addPlaceholder")
            }
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
          />
          <Button type="submit" disabled={adding || !urlInput.trim()} size="md">
            {adding ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : inputMode === "crawl" ? (
              <Globe className="size-4" aria-hidden="true" />
            ) : (
              <Plus className="size-4" aria-hidden="true" />
            )}
            {adding
              ? t("admin.sources.processing")
              : inputMode === "crawl"
              ? t("admin.sources.crawlButton")
              : t("admin.sources.addButton")}
          </Button>
        </div>

        {/* Depth selector — only shown in crawl mode */}
        {inputMode === "crawl" && (
          <div className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
            <label
              htmlFor="crawl-depth"
              className="text-xs font-medium text-muted-foreground whitespace-nowrap"
            >
              {t("admin.sources.depth")}
            </label>
            <input
              id="crawl-depth"
              type="range"
              min={0}
              max={4}
              value={crawlDepth}
              onChange={(e) => setCrawlDepth(Number(e.target.value))}
              className="flex-1 accent-primary"
            />
            <span className="min-w-[2rem] text-center text-xs font-bold text-foreground">
              {crawlDepth}
            </span>
            <span className="text-[10px] text-muted-foreground">
              {t(`admin.sources.depthHint${crawlDepth}` as any, t("admin.sources.depthHint1"))}
            </span>
          </div>
        )}
      </form>

      {/* Summary + action buttons */}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          {sources.length} {t("admin.sources.totalSources")}
        </span>
        {pendingCount > 0 && (
          <span className="rounded-full bg-muted px-2 py-0.5 font-medium">
            {pendingCount} {t("admin.sources.pending")}
          </span>
        )}
        {downloadedCount > 0 && (
          <span className="rounded-full bg-green-100 px-2 py-0.5 font-medium text-green-700 dark:bg-green-900/30 dark:text-green-400">
            {downloadedCount} {t("admin.sources.readyToIngest")}
          </span>
        )}
        {indexedCount > 0 && (
          <span className="rounded-full bg-green-600 px-2 py-0.5 font-medium text-white dark:bg-green-500/80">
            {indexedCount} {t("admin.sources.indexed")}
          </span>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <Button
          onClick={handleResearch}
          disabled={researching || pendingCount === 0}
          variant="outline"
          className="flex-1"
        >
          {researching ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <FileSearch className="size-4" aria-hidden="true" />
          )}
          {researching
            ? t("admin.sources.researching")
            : t("admin.sources.researchButton")}
        </Button>
        <Button
          onClick={handleIngest}
          disabled={ingesting || downloadedCount === 0}
          variant="outline"
          className="flex-1"
        >
          {ingesting ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Zap className="size-4" aria-hidden="true" />
          )}
          {ingesting
            ? t("admin.sources.ingesting")
            : t("admin.sources.ingestButton")}
        </Button>
      </div>

      {/* Live progress panel */}
      <ProgressPanel
        mode={progressMode}
        event={progressEvent}
        running={researching || ingesting}
      />

      {/* Results */}
      {researchResult && !researching && (
        <p className="text-xs text-green-600 dark:text-green-400">
          {researchResult}
        </p>
      )}
      {ingestResult && !ingesting && (
        <p className="text-xs text-green-600 dark:text-green-400">
          {ingestResult}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}

      {/* Sources table */}
      {loading ? (
        <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
      ) : sources.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("admin.sources.noSources")}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.url")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.file")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.statusLabel")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.size")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.score")}</th>
                <th className="pb-1.5 pr-2 font-medium">{t("admin.sources.chunks")}</th>
                <th className="pb-1.5 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((src) => (
                <tr
                  key={src.id}
                  className="border-b border-border/50 last:border-0"
                >
                  <td className="py-1.5 pr-2">
                    <a
                      href={src.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex max-w-[200px] items-center gap-1 truncate text-foreground underline-offset-2 hover:underline"
                      title={src.url}
                    >
                      {src.url.replace(/^https?:\/\//, "").slice(0, 40)}
                      {src.url.length > 47 ? "…" : ""}
                      <ExternalLink className="size-3 shrink-0 text-muted-foreground" />
                    </a>
                  </td>
                  <td className="max-w-[120px] truncate py-1.5 pr-2 font-mono text-muted-foreground">
                    {src.filename ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${STATUS_STYLES[src.status]}`}
                    >
                      {(src.status === "downloading" ||
                        src.status === "ingesting") && (
                        <Loader2 className="size-3 animate-spin" />
                      )}
                      {t(`admin.sources.statusLabels.${src.status}`)}
                    </span>
                    {src.error_message && (
                      <p
                        className="mt-0.5 max-w-[180px] truncate text-destructive"
                        title={src.error_message}
                      >
                        {src.error_message}
                      </p>
                    )}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {formatSize(src.file_size)}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {src.master_score != null
                      ? src.master_score.toFixed(1)
                      : "—"}
                  </td>
                  <td className="py-1.5 pr-2 text-muted-foreground">
                    {src.chunks_indexed ?? "—"}
                  </td>
                  <td className="py-1.5">
                    <button
                      onClick={() => handleDelete(src.id)}
                      className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                      title={t("admin.sources.delete")}
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

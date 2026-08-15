import { useMemo, useState } from "react";
import {
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Source } from "../../types";

interface Props {
  sources: Source[];
}

/** Formats all sources as a plain-text block for the clipboard. */
export function formatSourcesForClipboard(sources: Source[]): string {
  const byFile = new Map<string, Source[]>();
  for (const source of sources) {
    const list = byFile.get(source.source_file) ?? [];
    list.push(source);
    byFile.set(source.source_file, list);
  }

  const lines: string[] = [];
  for (const [file, chunks] of byFile) {
    const date = chunks.find((c) => c.date)?.date;
    lines.push(`📄 ${file}${date ? ` (${date})` : ""}`);
    for (const chunk of chunks) {
      lines.push(`   p.${chunk.page} — ${chunk.content.trim()}`);
    }
    lines.push("");
  }
  return lines.join("\n").trim();
}

/**
 * Collapsible "Sources" panel rendered under an assistant answer.
 *
 * Chunks are grouped by document, and each group shows the file name, a
 * best-effort publication date (parsed from the filename server-side), and
 * every retrieved chunk with its page number + content snippet. Supports
 * copying the full source list and expanding individual chunks to their
 * full text.
 */
export default function SourcesDropdown({ sources }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  // Chunk indexes (flat over all sources) whose full text is expanded.
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Group chunks by file, preserving first-seen order.
  const groups = useMemo(() => {
    const byFile = new Map<string, Source[]>();
    for (const source of sources) {
      const list = byFile.get(source.source_file) ?? [];
      list.push(source);
      byFile.set(source.source_file, list);
    }
    return Array.from(byFile.entries());
  }, [sources]);

  if (sources.length === 0) return null;

  const copySources = async () => {
    const text = formatSourcesForClipboard(sources);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback for non-secure contexts (e.g. http://localhost over plain HTTP).
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  const toggleChunk = (index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  let flatIndex = -1;

  return (
    <div className="mt-3" data-testid="sources-dropdown">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="sources-panel"
          className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/5 px-2.5 py-1 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10"
        >
          <FileText className="size-3" aria-hidden="true" />
          {t("chat.sources")} ({sources.length})
          <ChevronDown
            className={`size-3 transition-transform ${open ? "rotate-180" : ""}`}
            aria-hidden="true"
          />
        </button>

        {open && (
          <button
            type="button"
            onClick={copySources}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-muted"
          >
            {copied ? (
              <Check className="size-3 text-green-500" aria-hidden="true" />
            ) : (
              <Copy className="size-3" aria-hidden="true" />
            )}
            {copied ? t("chat.copied") : t("chat.copySources")}
          </button>
        )}
      </div>

      {open && (
        <div
          id="sources-panel"
          className="mt-2 space-y-3 rounded-xl border border-border bg-background p-3"
        >
          {groups.map(([file, chunks]) => {
            const date = chunks.find((c) => c.date)?.date;
            return (
              <div key={file} className="flex flex-col gap-1.5">
                {/* File header: name + date */}
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[10px] font-semibold text-foreground">
                    {file}
                  </span>
                  {date && (
                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[9px] font-medium text-muted-foreground">
                      <CalendarDays className="size-3" aria-hidden="true" />
                      {date}
                    </span>
                  )}
                </div>

                {/* Chunks from this document */}
                <ul className="space-y-1.5">
                  {chunks.map((chunk) => {
                    flatIndex += 1;
                    const index = flatIndex;
                    const isExpanded = expanded.has(index);
                    return (
                      <li
                        key={`${file}-${index}`}
                        className="rounded-lg border border-border/60 bg-card px-2.5 py-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[9px] font-bold uppercase tracking-wide text-primary">
                            p.{chunk.page}
                          </span>
                          {chunk.content.trim().length > 140 && (
                            <button
                              type="button"
                              onClick={() => toggleChunk(index)}
                              aria-expanded={isExpanded}
                              className="inline-flex shrink-0 items-center gap-0.5 text-[9px] font-medium text-primary transition-colors hover:text-primary/70"
                            >
                              {isExpanded ? (
                                <>
                                  {t("chat.viewLess")}
                                  <ChevronUp className="size-2.5" aria-hidden="true" />
                                </>
                              ) : (
                                <>
                                  {t("chat.viewMore")}
                                  <ChevronDown className="size-2.5" aria-hidden="true" />
                                </>
                              )}
                            </button>
                          )}
                        </div>
                        <p
                          className={`mt-0.5 text-[11px] leading-relaxed text-muted-foreground ${
                            isExpanded ? "" : "line-clamp-3"
                          }`}
                        >
                          {chunk.content}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import { FormEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileUp, Link2, Loader2, UploadCloud } from "lucide-react";
import Button from "../ui/Button";
import {
  DocumentIngestResult,
  ingestDocumentFromUrl,
  uploadDocument,
} from "../../services/admin";

type Mode = "file" | "url";

export default function DocumentUpload() {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<Mode>("file");
  const [fileName, setFileName] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DocumentIngestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFileName(e.target.files?.[0]?.name ?? null);
    setResult(null);
    setError(null);
  };

  const handleUploadFile = async (e: FormEvent) => {
    e.preventDefault();
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await uploadDocument(file));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleIngestUrl = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await ingestDocumentFromUrl(trimmed));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-sm">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{t("admin.docs.title")}</h3>
        <p className="text-xs text-muted-foreground">{t("admin.docs.subtitle")}</p>
      </div>

      {/* Mode toggle: file or URL */}
      <div className="flex gap-1 rounded-lg bg-muted p-1">
        {(
          [
            ["file", "admin.docs.uploadFile"],
            ["url", "admin.docs.ingestUrl"],
          ] as [Mode, string][]
        ).map(([value, labelKey]) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setMode(value);
              setError(null);
              setResult(null);
            }}
            aria-pressed={mode === value}
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              mode === value
                ? "bg-background text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {value === "file" ? (
              <FileUp className="size-3.5" aria-hidden="true" />
            ) : (
              <Link2 className="size-3.5" aria-hidden="true" />
            )}
            {t(labelKey)}
          </button>
        ))}
      </div>

      {mode === "file" ? (
        <form onSubmit={handleUploadFile} className="flex flex-col gap-2">
          <label htmlFor="admin-doc-file" className="sr-only">
            {t("admin.docs.uploadFile")}
          </label>
          <input
            ref={fileInputRef}
            id="admin-doc-file"
            type="file"
            accept="application/pdf,.pdf"
            onChange={handleFileChange}
            className="block w-full cursor-pointer rounded-lg border border-border bg-background text-xs text-muted-foreground file:mr-3 file:cursor-pointer file:rounded-md file:border-0 file:bg-primary/10 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-primary"
          />
          <Button type="submit" disabled={busy || !fileName} className="w-full">
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <UploadCloud className="size-4" aria-hidden="true" />
            )}
            {busy ? t("admin.docs.processing") : t("admin.docs.uploadAction")}
          </Button>
        </form>
      ) : (
        <form onSubmit={handleIngestUrl} className="flex flex-col gap-2">
          <label htmlFor="admin-doc-url" className="sr-only">
            {t("admin.docs.ingestUrl")}
          </label>
          <input
            id="admin-doc-url"
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setError(null);
              setResult(null);
            }}
            placeholder="https://example.org/rapport.pdf"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary/50 focus:ring-2 focus:ring-primary/15"
          />
          <Button type="submit" disabled={busy || !url.trim()} className="w-full">
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Link2 className="size-4" aria-hidden="true" />
            )}
            {busy ? t("admin.docs.processing") : t("admin.docs.ingestAction")}
          </Button>
        </form>
      )}

      {error && <p className="text-xs text-destructive">{error}</p>}

      {result && (
        <div
          data-testid="doc-result"
          className={`rounded-lg border p-3 text-xs ${
            result.status === "PASSED"
              ? "border-green-500/40 bg-green-500/5 text-green-700 dark:text-green-400"
              : "border-destructive/40 bg-destructive/5 text-destructive"
          }`}
        >
          <p className="font-semibold">
            {result.status === "PASSED"
              ? t("admin.docs.accepted", { file: result.filename })
              : t("admin.docs.rejected", { file: result.filename })}
          </p>
          <ul className="mt-1.5 space-y-0.5 text-muted-foreground">
            <li>
              {t("admin.docs.destination")}:{" "}
              <span className="font-mono">
                {result.dest === "filtered"
                  ? t("admin.docs.filtered")
                  : t("admin.docs.blacklisted")}
              </span>
            </li>
            <li>
              {t("admin.docs.gate1")}: {result.gate1_score} ·{" "}
              {t("admin.docs.gate2")}: {result.gate2_score ?? "—"} ·{" "}
              {t("admin.docs.master")}: {result.master_score}
            </li>
            <li>
              {t("admin.docs.pages")}: {result.total_pages} ·{" "}
              {t("admin.docs.chunks")}: {result.chunks_indexed}
            </li>
          </ul>
          {result.index_error && (
            <p className="mt-1.5 text-destructive">
              {t("admin.docs.indexError")}: {result.index_error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

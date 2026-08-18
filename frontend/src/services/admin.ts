import axios from "axios";
import api from "./api";
import { getStoredToken } from "./token";

// Axios instance for multipart uploads — inherits no Content-Type default
// so the browser auto-sets multipart/form-data with the correct boundary.
const apiUpload = axios.create({
  baseURL: "/api",
  timeout: 300_000,
});
apiUpload.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) config.headers.set("Authorization", `Bearer ${token}`);
  return config;
});

// The admin API key is kept in memory only (never persisted to localStorage).
// The AdminTab login gate calls setAdminKey() before fetching stats.
let adminKey: string | null = null;

export function setAdminKey(key: string): void {
  adminKey = key.trim() || null;
}

export function getAdminKey(): string | null {
  return adminKey;
}

export function clearAdminKey(): void {
  adminKey = null;
}

export interface PurgeRun {
  run: number;
  at: string;
  deleted: number;
  error?: string | null;
}

export interface PurgeStats {
  ttl_hours: number;
  purge_interval_minutes: number;
  total_runs: number;
  total_deleted: number;
  last_run_at?: string | null;
  next_run_at?: string | null;
  recent_runs: PurgeRun[];
}

function adminHeaders(): Record<string, string> {
  return adminKey ? { "X-Admin-Key": adminKey } : {};
}

export async function fetchPurgeStats(): Promise<PurgeStats> {
  const { data } = await api.get<PurgeStats>("/admin/purge-stats", {
    headers: adminHeaders(),
  });
  return data;
}

export async function runPurgeNow(): Promise<{ status: string; deleted: number }> {
  const { data } = await api.post<{ status: string; deleted: number }>(
    "/admin/purge",
    undefined,
    { headers: adminHeaders() }
  );
  return data;
}

/** All runtime settings (admin-only, key-protected). */
export async function fetchAdminConfig(): Promise<Record<string, string>> {
  const { data } = await api.get<Record<string, string>>("/admin/config", {
    headers: adminHeaders(),
  });
  return data;
}

/** Upsert runtime settings (admin-only, key-protected). */
export async function updateAdminConfig(
  settings: Record<string, string>
): Promise<Record<string, string>> {
  const { data } = await api.put<Record<string, string>>(
    "/admin/config",
    { settings },
    { headers: adminHeaders() }
  );
  return data;
}

/** Public settings the dashboard needs (no key required). */
export async function fetchPublicConfig(): Promise<{ map_refresh_seconds: string }> {
  const { data } = await api.get<{ map_refresh_seconds: string }>("/config");
  return data;
}

export interface DocumentIngestResult {
  filename: string;
  status: "PASSED" | "BLACKLISTED";
  dest: "filtered" | "blacklisted";
  gate1_score: number;
  gate2_score?: number | null;
  master_score: number;
  total_pages: number;
  chunks_indexed: number;
  index_error?: string | null;
}

/** Admin-only: upload a PDF file -> triage -> route -> index (if accepted).
 * Multipart: axios sets the boundary when given a FormData body. */
export async function uploadDocument(file: File): Promise<DocumentIngestResult> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiUpload.post<DocumentIngestResult>("/admin/documents/upload", form, {
    headers: adminHeaders(),
  });
  return data;
}

/** Admin-only: ingest a PDF from a URL -> triage -> route -> index. */
export async function ingestDocumentFromUrl(url: string): Promise<DocumentIngestResult> {
  const { data } = await api.post<DocumentIngestResult>(
    "/admin/documents/from-url",
    { url },
    { headers: adminHeaders(), timeout: 300_000 }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Admin: URL source management (research + ingest)
// ---------------------------------------------------------------------------

export type SourceStatus =
  | "pending"
  | "downloading"
  | "downloaded"
  | "failed"
  | "ingesting"
  | "indexed"
  | "triage_rejected";

export interface Source {
  id: string;
  url: string;
  filename: string | null;
  status: SourceStatus;
  file_size: number | null;
  total_pages: number | null;
  gate1_score: number | null;
  master_score: number | null;
  chunks_indexed: number | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResearchResult {
  downloaded: number;
  failed: number;
  total: number;
}

export interface IngestResult {
  indexed: number;
  rejected: number;
  failed: number;
  total: number;
}

/** List all tracked sources. */
export async function fetchSources(): Promise<Source[]> {
  const { data } = await api.get<Source[]>("/admin/sources", {
    headers: adminHeaders(),
  });
  return data;
}

/** Add a PDF URL to the research queue. */
export async function addSource(url: string): Promise<Source> {
  const { data } = await api.post<Source>(
    "/admin/sources",
    { url },
    { headers: adminHeaders() }
  );
  return data;
}

/** Remove a source from the tracking table. */
export async function deleteSource(id: string): Promise<void> {
  await api.delete(`/admin/sources/${id}`, { headers: adminHeaders() });
}

/** Download + validate all pending sources. */
export async function researchSources(): Promise<ResearchResult> {
  const { data } = await api.post<ResearchResult>(
    "/admin/sources/research",
    undefined,
    { headers: adminHeaders(), timeout: 1_800_000 }  // 30 minutes for large batches
  );
  return data;
}

/** Triage + ChromaDB index all downloaded sources. */
export async function ingestSources(): Promise<IngestResult> {
  const { data } = await api.post<IngestResult>(
    "/admin/sources/ingest",
    undefined,
    { headers: adminHeaders(), timeout: 1_800_000 }  // 30 minutes for large batches
  );
  return data;
}

export interface CrawlResult {
  found: number;
  added: number;
  skipped: number;
  pdf_urls: string[];
}

/** Crawl a website page for PDF links and add them as sources. */
export async function crawlWebsite(url: string): Promise<CrawlResult> {
  const { data } = await api.post<CrawlResult>(
    "/admin/sources/crawl",
    { url },
    { headers: adminHeaders(), timeout: 60_000 }
  );
  return data;
}

/** Delete multiple sources by IDs or by status. */
export async function bulkDeleteSources(opts: {
  ids?: string[];
  status?: SourceStatus;
}): Promise<{ deleted: number }> {
  const { data } = await api.post<{ deleted: number }>(
    "/admin/sources/bulk-delete",
    opts,
    { headers: adminHeaders() }
  );
  return data;
}

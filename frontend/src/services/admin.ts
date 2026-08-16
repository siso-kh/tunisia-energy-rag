import api from "./api";

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

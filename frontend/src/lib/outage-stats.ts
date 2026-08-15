import type { OutageStatus } from "../types";

/**
 * Pure helpers for the TunisiaMap live-outage wiring. Kept dependency-free
 * so they can be unit-tested without rendering Leaflet or the SVG.
 */

/** "Gabès" -> "gabes", "Médenine" -> "medenine" (for region <-> node matching). */
export function normalizeRegion(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

/** Count outage reports per governorate, keyed by *normalized* region name. */
export function countOutagesByRegion(
  reports: Array<{ region: string }>
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const report of reports) {
    const key = normalizeRegion(report.region);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

/** Per-status breakdown for a single region. */
export type StatusCounts = Record<OutageStatus, number>;

const EMPTY_STATUS: StatusCounts = { PENDING: 0, VERIFIED: 0, RESOLVED: 0 };

/** Count outages per governorate AND per status, keyed by normalized region. */
export function countOutagesByStatus(
  reports: Array<{ region: string; status: OutageStatus }>
): Record<string, StatusCounts> {
  const counts: Record<string, StatusCounts> = {};
  for (const report of reports) {
    const key = normalizeRegion(report.region);
    const entry = (counts[key] ??= { ...EMPTY_STATUS });
    entry[report.status] += 1;
  }
  return counts;
}

export function totalStatusCount(counts: StatusCounts): number {
  return counts.PENDING + counts.VERIFIED + counts.RESOLVED;
}

/** One arc of the status-mix donut badge. */
export interface StatusSegment {
  status: OutageStatus;
  count: number;
  /** 0..1 share of the full ring. */
  fraction: number;
  /** Start angle in radians (clockwise from 3 o'clock). */
  offset: number;
}

const SEGMENT_ORDER: OutageStatus[] = ["PENDING", "VERIFIED", "RESOLVED"];

/** Split a status breakdown into clockwise ring segments (skip zero counts). */
export function statusSegments(counts: StatusCounts): StatusSegment[] {
  const total = totalStatusCount(counts);
  if (total <= 0) return [];
  let acc = 0;
  const segments: StatusSegment[] = [];
  for (const status of SEGMENT_ORDER) {
    const count = counts[status];
    if (count <= 0) continue;
    const fraction = count / total;
    segments.push({ status, count, fraction, offset: acc * 2 * Math.PI });
    acc += fraction;
  }
  return segments;
}

/** Badge ring colors keyed by outage status (single source of truth). */
export const STATUS_COLORS: Record<OutageStatus, string> = {
  PENDING: "#f59e0b",
  VERIFIED: "#ef4444",
  RESOLVED: "#22c55e",
};

/** Human-readable badge number, capped at "99+" for huge counts. */
export function badgeText(count: number): string {
  return count > 99 ? "99+" : String(count);
}

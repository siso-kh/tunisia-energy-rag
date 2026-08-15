import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { MAP_HEIGHT, MAP_WIDTH, cityNodes, tunisiaPath, type Sector } from "../../lib/tunisia-geo";
import {
  badgeText,
  normalizeRegion,
  statusSegments,
  STATUS_COLORS,
  totalStatusCount,
  type StatusCounts,
} from "../../lib/outage-stats";

const SECTOR_COLOR: Record<Sector, string> = {
  solar: "var(--solar)",
  electric: "var(--electric)",
  water: "var(--water)",
  graphite: "var(--graphite)",
};

const BADGE_RADIUS = 7;
const BADGE_CIRCUMFERENCE = 2 * Math.PI * BADGE_RADIUS;

interface Props {
  /**
   * Per-governorate outage breakdowns (normalized keys), e.g.
   * { "tunis": { PENDING: 2, VERIFIED: 1, RESOLVED: 0 } }. The badge ring is
   * colored by the status mix; nodes with zero outages keep their static label.
   */
  outageStatus?: Record<string, StatusCounts>;
  /** Called with the canonical city name when a node is clicked. */
  onNodeClick?: (region: string) => void;
  /** Governorate currently selected (highlights its node with a ring). */
  selectedRegion?: string | null;
}

/**
 * Stylized animated SVG map of Tunisia with live utility telemetry nodes
 * (ported from the ATER dashboard). When `outageStatus` is provided, each node
 * shows a status-mix donut badge + "N signalements" label reflecting real data.
 * Nodes are clickable to filter the outage map by governorate.
 */
export default function TunisiaMap({ outageStatus, onNodeClick, selectedRegion }: Props) {
  const { t } = useTranslation();

  // Normalize the incoming region keys once for O(1) lookups per node.
  const statusByRegion = useMemo(() => {
    const map = new Map<string, StatusCounts>();
    if (!outageStatus) return map;
    for (const [region, counts] of Object.entries(outageStatus)) {
      map.set(normalizeRegion(region), counts);
    }
    return map;
  }, [outageStatus]);

  const selectedNormalized = selectedRegion ? normalizeRegion(selectedRegion) : null;

  return (
    <div className="relative overflow-hidden rounded-xl border border-border bg-card p-2">
      <svg
        viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
        className="h-auto w-full drop-shadow-[0_12px_28px_rgba(37,99,235,0.14)]"
        role="img"
        aria-label="Stylized map of Tunisia showing live utility telemetry nodes in Tunis, Sousse, Sfax, Gabès and other cities"
      >
        <defs>
          <linearGradient id="landFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" />
            <stop offset="55%" stopColor="#2563eb" />
            <stop offset="100%" stopColor="#1e40af" />
          </linearGradient>
          <linearGradient id="landStroke" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="100%" stopColor="#60a5fa" />
          </linearGradient>
        </defs>

        {/* soft echo silhouettes for a layered, 3D-like feel */}
        <path d={tunisiaPath} transform="translate(10 14)" fill="rgba(34,211,238,0.14)" />
        <path d={tunisiaPath} transform="translate(5 7)" fill="rgba(37,99,235,0.16)" />

        <path
          d={tunisiaPath}
          fill="url(#landFill)"
          stroke="url(#landStroke)"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />

        {cityNodes.map((c) => {
          const counts = statusByRegion.get(normalizeRegion(c.name));
          const total = counts ? totalStatusCount(counts) : 0;
          const segments = counts ? statusSegments(counts) : [];
          const isSelected = selectedNormalized === normalizeRegion(c.name);
          const clickable = total > 0;

          return (
            <g
              key={c.name}
              onClick={clickable ? () => onNodeClick?.(c.name) : undefined}
              onKeyDown={
                clickable
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onNodeClick?.(c.name);
                      }
                    }
                  : undefined
              }
              role={clickable ? "button" : undefined}
              tabIndex={clickable ? 0 : undefined}
              aria-label={
                clickable
                  ? t("map.filterRegion", { region: c.name })
                  : undefined
              }
              aria-pressed={clickable ? isSelected : undefined}
              className={clickable ? "cursor-pointer outline-none focus-visible:opacity-80" : undefined}
            >
              {/* selection ring */}
              {isSelected && (
                <circle
                  cx={c.x}
                  cy={c.y}
                  r={11}
                  fill="none"
                  stroke="#2563eb"
                  strokeWidth={2}
                  strokeDasharray="3 2"
                />
              )}

              {/* pulsing node */}
              <g transform={`translate(${c.x} ${c.y})`}>
                <circle
                  r={7}
                  fill={SECTOR_COLOR[c.sector]}
                  style={{
                    transformOrigin: "center",
                    animation: `node-pulse 3s ease-in-out ${(c.x % 5) * 0.35}s infinite`,
                  }}
                />
                <circle r={3.4} fill="white" />
                <circle r={2} fill={SECTOR_COLOR[c.sector]} />
              </g>

              {/* status-mix donut badge (top-left, clear of the label card) */}
              {total > 0 && (
                <g transform={`translate(${c.x - 12} ${c.y - 12})`}>
                  {/* white base ring so arcs read cleanly over the map */}
                  <circle r={BADGE_RADIUS} fill="none" stroke="white" strokeWidth={4} />
                  {/* status arcs, clockwise from 3 o'clock */}
                  {segments.map((seg) => (
                    <circle
                      key={seg.status}
                      r={BADGE_RADIUS}
                      fill="none"
                      stroke={STATUS_COLORS[seg.status]}
                      strokeWidth={4}
                      strokeDasharray={`${seg.fraction * BADGE_CIRCUMFERENCE} ${BADGE_CIRCUMFERENCE}`}
                      transform={`rotate(${(seg.offset * 180) / Math.PI} 0 0)`}
                    />
                  ))}
                  <circle r={4.6} fill="white" />
                  <text
                    textAnchor="middle"
                    y={1.8}
                    fontSize={7.5}
                    fontWeight={700}
                    fill="#0f172a"
                  >
                    {badgeText(total)}
                  </text>
                </g>
              )}

              {/* city label */}
              <g transform={`translate(${c.x} ${c.y})`}>
                <foreignObject x={10} y={-14} width={128} height={30} className="overflow-visible">
                  <div className="inline-flex flex-col rounded-md bg-card/90 px-2 py-0.5 shadow-sm ring-1 ring-border backdrop-blur">
                    <span className="text-[10px] font-semibold leading-tight text-foreground">
                      {c.name}
                    </span>
                    <span
                      className="text-[8px] font-medium leading-tight"
                      style={{ color: SECTOR_COLOR[c.sector] }}
                    >
                      {total > 0 ? t("map.outageCount", { count: total }) : c.label}
                    </span>
                  </div>
                </foreignObject>
              </g>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { MapPin, X } from "lucide-react";
import OutageMap from "../map/OutageMap";
import TunisiaMap from "../map/TunisiaMap";
import ReportForm from "../map/ReportForm";
import { fetchOutages } from "../../services/outages";
import { countOutagesByStatus, normalizeRegion, STATUS_COLORS } from "../../lib/outage-stats";
import type { OutageStatus } from "../../types";

const FILTERS: Array<{ key: OutageStatus | "ALL"; label: string }> = [
  { key: "ALL", label: "map.all" },
  { key: "PENDING", label: "map.pending" },
  { key: "VERIFIED", label: "map.verified" },
  { key: "RESOLVED", label: "map.resolved" },
];

const STATUS_LEGEND = [
  { label: "map.pending", color: STATUS_COLORS.PENDING },
  { label: "map.verified", color: STATUS_COLORS.VERIFIED },
  { label: "map.resolved", color: STATUS_COLORS.RESOLVED },
];

const SECTOR_LEGEND = [
  { label: "map.sectorSolar", color: "var(--solar)" },
  { label: "map.sectorElectric", color: "var(--electric)" },
  { label: "map.sectorWater", color: "var(--water)" },
  { label: "map.sectorPetrol", color: "var(--graphite)" },
];

export default function CarteTab() {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<OutageStatus | "ALL">("ALL");
  const [picked, setPicked] = useState<{ lat: number; lng: number } | null>(null);
  // Governorate selected by clicking a TunisiaMap node (canonical name, e.g. "Tunis").
  const [selectedRegion, setSelectedRegion] = useState<string | null>(null);

  const { data: reports = [], isLoading } = useQuery({
    queryKey: ["outages", filter],
    queryFn: () => fetchOutages(filter === "ALL" ? null : filter),
    // keep the map live without blocking the UI
    refetchInterval: 60_000,
  });

  // Clicking the selected node again (or the chip's ✕) clears the filter.
  const toggleRegion = (region: string) => {
    setSelectedRegion((prev) =>
      prev && normalizeRegion(prev) === normalizeRegion(region) ? null : region
    );
  };

  // Leaflet markers honour BOTH the status filter and the governorate selection.
  const markers = useMemo(() => {
    if (!selectedRegion) return reports;
    const wanted = normalizeRegion(selectedRegion);
    return reports.filter((r) => normalizeRegion(r.region) === wanted);
  }, [reports, selectedRegion]);

  // Per-governorate per-status breakdowns for the animated overview map
  // (reflects the active status filter, since it derives from the same
  // fetched reports). The badge ring colors mirror the status mix.
  const statusByRegion = useMemo(() => countOutagesByStatus(reports), [reports]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{t("map.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("map.subtitle")}</p>
        </div>
        <span className="flex size-8 items-center justify-center rounded-lg bg-electric/10 text-electric">
          <MapPin className="size-4" aria-hidden="true" />
        </span>
      </div>

      {/* Animated overview map (ATER TunisiaMap) with live outage counts.
          Clicking a node filters the interactive map below. */}
      <TunisiaMap
        outageStatus={statusByRegion}
        onNodeClick={toggleRegion}
        selectedRegion={selectedRegion}
      />

      {/* Sector legend */}
      <div className="grid grid-cols-2 gap-2">
        {SECTOR_LEGEND.map((l) => (
          <div key={l.label} className="flex items-center gap-2 rounded-lg border border-border bg-card px-2.5 py-2">
            <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: l.color }} />
            <span className="truncate text-[11px] font-medium text-foreground">{t(l.label)}</span>
          </div>
        ))}
      </div>

      {/* Status filters */}
      <div className="flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors ${
              filter === f.key
                ? "bg-primary text-primary-foreground"
                : "border border-border bg-background text-muted-foreground hover:text-foreground"
            }`}
          >
            {t(f.label)}
          </button>
        ))}
      </div>

      {/* Interactive outage map */}
      <div className="relative h-[280px] overflow-hidden rounded-xl border border-border">
        {isLoading ? (
          <div className="absolute inset-0 flex items-center justify-center bg-background text-xs text-muted-foreground">
            {t("common.loading")}
          </div>
        ) : (
          <OutageMap reports={markers} onPick={(lat, lng) => setPicked({ lat, lng })} />
        )}

        {/* Governorate filter chip */}
        {selectedRegion && (
          <div className="absolute start-2 top-2 z-[500] flex items-center gap-1.5 rounded-lg border border-primary/30 bg-card/95 px-2.5 py-1.5 text-[11px] font-medium text-primary shadow-sm backdrop-blur">
            <MapPin className="size-3" aria-hidden="true" />
            <span>{t("map.filteringBy")}</span>
            <span className="font-bold">{selectedRegion}</span>
            <button
              type="button"
              onClick={() => setSelectedRegion(null)}
              className="ms-1 flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label={t("map.clearFilter")}
            >
              <X className="size-3" aria-hidden="true" />
            </button>
          </div>
        )}
      </div>

      {/* Status legend */}
      <div className="grid grid-cols-3 gap-2">
        {STATUS_LEGEND.map((l) => (
          <div key={l.label} className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-2 py-1.5">
            <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: l.color }} />
            <span className="truncate text-[11px] font-medium text-foreground">{t(l.label)}</span>
          </div>
        ))}
      </div>

      {/* Report form */}
      <ReportForm picked={picked} />
    </div>
  );
}

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CarteTab from "./CarteTab";
import { fetchOutages } from "../../services/outages";
import type { OutageReport, OutageStatus } from "../../types";

// --- fixture data ----------------------------------------------------------

const base = (over: Partial<OutageReport>): OutageReport => ({
  id: "x",
  utility: "STEG",
  region: "Tunis",
  latitude: 36.8,
  longitude: 10.18,
  description: null,
  status: "PENDING",
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const ALL_REPORTS: OutageReport[] = [
  base({ id: "1", region: "Tunis", status: "PENDING" }),
  base({ id: "2", region: "Tunis", status: "VERIFIED" }),
  base({ id: "3", region: "Sousse", status: "PENDING" }),
  base({ id: "4", region: "Sfax", status: "RESOLVED" }),
];

// --- mocks -----------------------------------------------------------------

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "map.title": "Carte des pannes",
        "map.subtitle": "Signalements",
        "map.all": "Toutes",
        "map.pending": "En attente",
        "map.verified": "Vérifiées",
        "map.resolved": "Résolues",
        "map.sectorSolar": "Solaire / ANME",
        "map.sectorElectric": "Électricité / STEG",
        "map.sectorWater": "Eau / SONEDE",
        "map.sectorPetrol": "Pétrole / ETAP",
        "map.filteringBy": "Filtre :",
        "map.clearFilter": "Effacer le filtre",
        "map.filterRegion": "Filtrer par {{region}}",
        "common.loading": "Chargement…",
      })[key] ?? key,
  }),
}));

// Re-render with the current queryFn whenever the queryKey changes, so a
// filter click re-invokes fetchOutages with the new status.
vi.mock("@tanstack/react-query", () => ({
  useQuery: (options: { queryFn: () => unknown }) => ({
    data: options.queryFn(),
    isLoading: false,
  }),
}));

// Service returns different data depending on the requested status.
vi.mock("../../services/outages", () => ({
  fetchOutages: vi.fn((status?: OutageStatus | null) => {
    if (!status) return ALL_REPORTS;
    return ALL_REPORTS.filter((r) => r.status === status);
  }),
}));

// Stub the Leaflet map (needs real DOM) and the report form; expose the
// props they receive so we can assert on them.
vi.mock("../map/OutageMap", () => ({
  default: ({ reports }: { reports: OutageReport[] }) => (
    <div data-testid="outage-map">reports:{reports.length}</div>
  ),
}));

// TunisiaMap mock: renders one clickable button per governorate (derived from
// the live reports prop) so the click-to-filter flow can be exercised
// end-to-end within CarteTab.
vi.mock("../map/TunisiaMap", () => ({
  default: ({
    reports,
    onNodeClick,
    selectedRegion,
  }: {
    reports?: OutageReport[];
    onNodeClick?: (region: string) => void;
    selectedRegion?: string | null;
  }) => {
    const regions = Array.from(
      new Set((reports ?? []).map((r) => r.region.toLowerCase()))
    );
    return (
      <div data-testid="tunisia-map">
        <span data-testid="tunisia-selected">{selectedRegion ?? ""}</span>
        {regions.map((region) => (
          <button
            key={region}
            type="button"
            onClick={() => onNodeClick?.(region)}
          >
            node:{region}
          </button>
        ))}
      </div>
    );
  },
}));

vi.mock("../map/ReportForm", () => ({
  default: () => <div data-testid="report-form" />,
}));

// --- helpers ---------------------------------------------------------------

const markerCount = () =>
  screen.getByTestId("outage-map").textContent ?? "";

// --- tests -----------------------------------------------------------------

describe("CarteTab filter interaction", () => {
  beforeEach(() => {
    vi.mocked(fetchOutages).mockClear();
  });

  it("fetches all outages initially and passes full per-status counts to TunisiaMap", async () => {
    render(<CarteTab />);

    expect(fetchOutages).toHaveBeenCalledWith(null);
    // Every region with data gets a clickable node button
    expect(screen.getByRole("button", { name: "node:tunis" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "node:sousse" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "node:sfax" })).toBeInTheDocument();
    expect(markerCount()).toBe("reports:4");
  });

  it("re-queries with PENDING and narrows the counts when the filter changes", async () => {
    const user = userEvent.setup();
    render(<CarteTab />);

    await user.click(screen.getByRole("button", { name: "En attente" }));

    expect(fetchOutages).toHaveBeenCalledWith("PENDING");
    await waitFor(() => expect(screen.queryByRole("button", { name: "node:sfax" })).not.toBeInTheDocument());
    // sfax's RESOLVED outage is filtered out of the Leaflet map too
    expect(markerCount()).toBe("reports:2");
  });

  it("switching to RESOLVED shows only resolved regions", async () => {
    const user = userEvent.setup();
    render(<CarteTab />);

    await user.click(screen.getByRole("button", { name: "Résolues" }));

    expect(fetchOutages).toHaveBeenCalledWith("RESOLVED");
    await waitFor(() => expect(screen.getByRole("button", { name: "node:sfax" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "node:tunis" })).not.toBeInTheDocument();
    expect(markerCount()).toBe("reports:1");
  });

  describe("click-to-filter by governorate", () => {
    it("clicking a TunisiaMap node narrows the Leaflet markers to that region", async () => {
      const user = userEvent.setup();
      render(<CarteTab />);

      await user.click(screen.getByRole("button", { name: "node:tunis" }));

      // Tunis has 2 reports; the other regions' markers are hidden
      expect(markerCount()).toBe("reports:2");
      expect(screen.getByTestId("tunisia-selected").textContent).toBe("tunis");
    });

    it("clicking the same node again clears the governorate filter", async () => {
      const user = userEvent.setup();
      render(<CarteTab />);

      await user.click(screen.getByRole("button", { name: "node:tunis" }));
      expect(markerCount()).toBe("reports:2");

      await user.click(screen.getByRole("button", { name: "node:tunis" }));
      expect(markerCount()).toBe("reports:4");
      expect(screen.getByTestId("tunisia-selected").textContent).toBe("");
    });

    it("clears the filter via the ✕ chip", async () => {
      const user = userEvent.setup();
      render(<CarteTab />);

      await user.click(screen.getByRole("button", { name: "node:sousse" }));
      expect(markerCount()).toBe("reports:1");

      await user.click(screen.getByRole("button", { name: "Effacer le filtre" }));
      expect(markerCount()).toBe("reports:4");
      expect(screen.getByTestId("tunisia-selected").textContent).toBe("");
    });
  });
});

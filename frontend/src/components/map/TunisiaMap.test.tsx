import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import TunisiaMap from "./TunisiaMap";
import type { StatusCounts } from "../../lib/outage-stats";

// Stub react-i18next (i18n is exercised end-to-end elsewhere). Reproduce the
// plural resolution that i18next performs on t("map.outageCount", { count }).
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { count?: number; region?: string }) => {
      if (key === "map.outageCount") {
        const count = opts?.count ?? 0;
        return count === 1 ? "1 signalement" : `${count} signalements`;
      }
      if (key === "map.filterRegion") {
        return `Filtrer par ${opts?.region}`;
      }
      return key;
    },
  }),
}));

const counts = (p: number, v: number, r: number): StatusCounts => ({
  PENDING: p,
  VERIFIED: v,
  RESOLVED: r,
});

const clickNode = (name: string) => {
  fireEvent.click(screen.getByRole("button", { name: `Filtrer par ${name}` }));
};

describe("TunisiaMap", () => {
  it("renders the SVG with a descriptive aria-label", () => {
    const { container } = render(<TunisiaMap />);
    expect(screen.getByRole("img", { name: /Stylized map of Tunisia/i })).toBeInTheDocument();
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("shows static sector labels when no outage data is provided", () => {
    render(<TunisiaMap />);
    // Static labels from tunisia-geo (Tunis = electric, Sousse = solar)
    expect(screen.getByText("STEG · 1.8 GW")).toBeInTheDocument();
    expect(screen.getByText("PV · 320 MW")).toBeInTheDocument();
    // No badges at all
    expect(document.querySelectorAll('circle[stroke="#f59e0b"]').length).toBe(0);
  });

  it("renders a single-status donut + live label per region with outages", () => {
    const { container } = render(
      <TunisiaMap outageStatus={{ tunis: counts(1, 0, 0), sousse: counts(2, 0, 0) }} />
    );

    // Live labels replace the static ones
    expect(screen.getByText("1 signalement")).toBeInTheDocument();
    expect(screen.getByText("2 signalements")).toBeInTheDocument();

    // Two badges, each with one PENDING arc (whole ring)
    expect(container.querySelectorAll('circle[stroke="#f59e0b"]').length).toBe(2);

    // Static label is gone for a region with outages
    expect(screen.queryByText("STEG · 1.8 GW")).not.toBeInTheDocument();
  });

  it("renders a multi-status donut reflecting the status mix", () => {
    const { container } = render(
      <TunisiaMap outageStatus={{ tunis: counts(2, 1, 1) }} />
    );

    // All three status colors present on the single Tunis badge
    expect(container.querySelectorAll('circle[stroke="#f59e0b"]').length).toBe(1); // PENDING
    expect(container.querySelectorAll('circle[stroke="#ef4444"]').length).toBe(1); // VERIFIED
    expect(container.querySelectorAll('circle[stroke="#22c55e"]').length).toBe(1); // RESOLVED
    expect(screen.getByText("4 signalements")).toBeInTheDocument();
  });

  it("matches regions accent-insensitively (Gabès)", () => {
    const { container } = render(
      <TunisiaMap outageStatus={{ "gabès": counts(3, 0, 0) }} />
    );
    expect(screen.getByText("3 signalements")).toBeInTheDocument();
    expect(container.querySelectorAll('circle[stroke="#f59e0b"]').length).toBe(1);
  });

  it("caps huge counts at 99+", () => {
    render(<TunisiaMap outageStatus={{ tunis: counts(150, 0, 0) }} />);
    expect(screen.getByText("99+")).toBeInTheDocument();
  });

  it("leaves nodes with zero outages at their static label", () => {
    render(<TunisiaMap outageStatus={{ tunis: counts(1, 0, 0) }} />);
    // Sousse has no outage -> keeps its static solar label
    expect(screen.getByText("PV · 320 MW")).toBeInTheDocument();
    // Tunis -> live label instead
    expect(screen.queryByText("STEG · 1.8 GW")).not.toBeInTheDocument();
  });

  describe("click-to-filter", () => {
    it("calls onNodeClick with the canonical city name when a node is clicked", () => {
      const onNodeClick = vi.fn();
      render(
        <TunisiaMap
          outageStatus={{ tunis: counts(1, 0, 0) }}
          onNodeClick={onNodeClick}
        />
      );
      clickNode("Tunis");
      expect(onNodeClick).toHaveBeenCalledWith("Tunis");
    });

    it("marks the selected node as pressed and renders a selection ring", () => {
      const { container } = render(
        <TunisiaMap
          outageStatus={{ tunis: counts(1, 0, 0), sousse: counts(1, 0, 0) }}
          selectedRegion="Tunis"
        />
      );
      const tunis = screen.getByRole("button", { name: "Filtrer par Tunis" });
      expect(tunis).toHaveAttribute("aria-pressed", "true");
      // Selection ring = the dashed blue circle at the node
      const ring = container.querySelector('circle[stroke="#2563eb"]');
      expect(ring).toBeInTheDocument();
    });

    it("does not mark unselected nodes as pressed", () => {
      render(
        <TunisiaMap
          outageStatus={{ tunis: counts(1, 0, 0), sousse: counts(1, 0, 0) }}
          selectedRegion="Tunis"
        />
      );
      const sousse = screen.getByRole("button", { name: "Filtrer par Sousse" });
      expect(sousse).toHaveAttribute("aria-pressed", "false");
    });

    it("supports keyboard activation (Enter)", () => {
      const onNodeClick = vi.fn();
      render(
        <TunisiaMap
          outageStatus={{ tunis: counts(1, 0, 0) }}
          onNodeClick={onNodeClick}
        />
      );
      fireEvent.keyDown(screen.getByRole("button", { name: "Filtrer par Tunis" }), {
        key: "Enter",
      });
      expect(onNodeClick).toHaveBeenCalledWith("Tunis");
    });
  });
});

import { describe, expect, it } from "vitest";
import {
  badgeText,
  countOutagesByRegion,
  countOutagesByStatus,
  normalizeRegion,
  statusSegments,
  totalStatusCount,
  STATUS_COLORS,
} from "./outage-stats";

describe("normalizeRegion", () => {
  it("strips accents and lowercases", () => {
    expect(normalizeRegion("Gabès")).toBe("gabes");
    expect(normalizeRegion("Médenine")).toBe("medenine");
  });

  it("trims surrounding whitespace", () => {
    expect(normalizeRegion("  Tunis  ")).toBe("tunis");
  });

  it("handles already-normalized input", () => {
    expect(normalizeRegion("Sfax")).toBe("sfax");
  });
});

describe("countOutagesByRegion", () => {
  it("counts reports per region", () => {
    const reports = [
      { region: "Tunis" },
      { region: "Sousse" },
      { region: "tunis" },
      { region: "  Tunis " },
      { region: "Gabès" },
    ];
    expect(countOutagesByRegion(reports)).toEqual({
      tunis: 3,
      sousse: 1,
      gabes: 1,
    });
  });

  it("returns an empty object for no reports", () => {
    expect(countOutagesByRegion([])).toEqual({});
  });

  it("is accent-insensitive (Gabès vs gabes count together)", () => {
    expect(countOutagesByRegion([{ region: "Gabès" }, { region: "gabes" }])).toEqual({
      gabes: 2,
    });
  });
});

describe("countOutagesByStatus", () => {
  it("breaks counts down by status per region", () => {
    const reports = [
      { region: "Tunis", status: "PENDING" },
      { region: "Tunis", status: "VERIFIED" },
      { region: "Tunis", status: "PENDING" },
      { region: "Sousse", status: "RESOLVED" },
      { region: "gabès", status: "PENDING" },
    ] as Array<{ region: string; status: "PENDING" | "VERIFIED" | "RESOLVED" }>;

    expect(countOutagesByStatus(reports)).toEqual({
      tunis: { PENDING: 2, VERIFIED: 1, RESOLVED: 0 },
      sousse: { PENDING: 0, VERIFIED: 0, RESOLVED: 1 },
      gabes: { PENDING: 1, VERIFIED: 0, RESOLVED: 0 },
    });
  });

  it("returns an empty object for no reports", () => {
    expect(countOutagesByStatus([])).toEqual({});
  });
});

describe("totalStatusCount", () => {
  it("sums all statuses", () => {
    expect(totalStatusCount({ PENDING: 2, VERIFIED: 1, RESOLVED: 3 })).toBe(6);
  });

  it("is zero for an empty breakdown", () => {
    expect(totalStatusCount({ PENDING: 0, VERIFIED: 0, RESOLVED: 0 })).toBe(0);
  });
});

describe("statusSegments", () => {
  it("returns one full ring segment for a single status", () => {
    const segs = statusSegments({ PENDING: 3, VERIFIED: 0, RESOLVED: 0 });
    expect(segs).toHaveLength(1);
    expect(segs[0]).toMatchObject({ status: "PENDING", count: 3, fraction: 1, offset: 0 });
  });

  it("splits the ring proportionally in PENDING → VERIFIED → RESOLVED order", () => {
    const segs = statusSegments({ PENDING: 2, VERIFIED: 1, RESOLVED: 1 });
    expect(segs.map((s) => s.status)).toEqual(["PENDING", "VERIFIED", "RESOLVED"]);
    expect(segs.map((s) => s.fraction)).toEqual([0.5, 0.25, 0.25]);
    // clockwise start offsets: 0, 0.5 turn, 0.75 turn
    expect(segs.map((s) => s.offset)).toEqual([0, Math.PI, 1.5 * Math.PI]);
  });

  it("skips zero-count statuses but keeps order of the rest", () => {
    const segs = statusSegments({ PENDING: 1, VERIFIED: 0, RESOLVED: 3 });
    expect(segs.map((s) => s.status)).toEqual(["PENDING", "RESOLVED"]);
    expect(segs.map((s) => s.fraction)).toEqual([0.25, 0.75]);
  });

  it("returns an empty array for a zero breakdown", () => {
    expect(statusSegments({ PENDING: 0, VERIFIED: 0, RESOLVED: 0 })).toEqual([]);
  });
});

describe("STATUS_COLORS", () => {
  it("provides a distinct color per status", () => {
    const colors = Object.values(STATUS_COLORS);
    expect(new Set(colors).size).toBe(3);
    expect(colors.every((c) => /^#[0-9a-f]{6}$/i.test(c))).toBe(true);
  });
});

describe("badgeText", () => {
  it("formats small counts as-is", () => {
    expect(badgeText(1)).toBe("1");
    expect(badgeText(99)).toBe("99");
  });

  it("caps large counts at 99+", () => {
    expect(badgeText(100)).toBe("99+");
    expect(badgeText(1234)).toBe("99+");
  });

  it("shows 0 as 0", () => {
    expect(badgeText(0)).toBe("0");
  });
});

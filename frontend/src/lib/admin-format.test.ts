import { describe, expect, it } from "vitest";
import { formatPurgeDate } from "./admin-format";

describe("formatPurgeDate", () => {
  it("formats an ISO timestamp to a localized date/time", () => {
    // Timezone-agnostic assertions: local rendering always contains the
    // year, a time of day, and a date separator.
    const out = formatPurgeDate("2026-08-15T23:18:22.188789+00:00");
    expect(out).toMatch(/26/);
    expect(out).toMatch(/\d{1,2}:\d{2}/);
    expect(out).toMatch(/[/.,\s]/);
  });

  it("returns the raw string for unparseable input", () => {
    expect(formatPurgeDate("not-a-date")).toBe("not-a-date");
  });

  it("handles missing milliseconds", () => {
    expect(() => formatPurgeDate("2026-08-15T23:18:22Z")).not.toThrow();
  });
});

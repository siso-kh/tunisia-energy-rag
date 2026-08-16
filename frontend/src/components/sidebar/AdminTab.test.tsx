import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminTab from "./AdminTab";
import {
  clearAdminKey,
  fetchPurgeStats,
  runPurgeNow,
  setAdminKey,
} from "../../services/admin";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { count?: number }) => {
      const map: Record<string, string> = {
        "admin.title": "Administration",
        "admin.subtitle": "Nettoyage",
        "admin.ttl": "Durée de vie",
        "admin.interval": "Intervalle",
        "admin.hours": "heures",
        "admin.minutes": "min",
        "admin.totalRuns": "Exécutions",
        "admin.totalDeleted": "Signalements purgés",
        "admin.lastRun": "Dernière exécution",
        "admin.nextRun": "Prochaine exécution",
        "admin.purgeNow": "Purger maintenant",
        "admin.purging": "Purge en cours…",
        "admin.recentRuns": "Historique récent",
        "admin.noRuns": "Aucune exécution.",
        "admin.failed": "Échec",
        "admin.deletedShort": "purgé(s)",
        "admin.apiKeyLabel": "Clé API administrateur",
        "admin.unlock": "Déverrouiller",
        "admin.invalidKey": "Clé API invalide.",
        "admin.tryAgain": "Réessayer",
        "common.loading": "Chargement…",
        "chat.error": "Erreur",
      };
      if (key === "admin.purged") {
        const n = opts?.count ?? 0;
        return n === 1 ? "✓ 1 signalement purgé" : `✓ ${n} signalements purgés`;
      }
      return map[key] ?? key;
    },
  }),
}));

vi.mock("../../services/admin", () => {
  let key: string | null = null;
  return {
    fetchPurgeStats: vi.fn(),
    runPurgeNow: vi.fn(),
    setAdminKey: vi.fn((k: string) => {
      key = k.trim() || null;
    }),
    getAdminKey: vi.fn(() => key),
    clearAdminKey: vi.fn(() => {
      key = null;
    }),
  };
});

function renderAdmin() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminTab />
    </QueryClientProvider>
  );
}

const STATS = {
  ttl_hours: 5,
  purge_interval_minutes: 30,
  total_runs: 2,
  total_deleted: 3,
  last_run_at: "2026-08-15T23:18:22Z",
  next_run_at: "2026-08-15T23:48:22Z",
  recent_runs: [
    { run: 2, at: "2026-08-15T23:18:22Z", deleted: 1, error: null },
    { run: 1, at: "2026-08-15T22:48:22Z", deleted: 0, error: null },
  ],
};

describe("AdminTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchPurgeStats).mockResolvedValue(STATS);
    vi.mocked(runPurgeNow).mockResolvedValue({ status: "ok", deleted: 1 });
    clearAdminKey();
  });

  afterEach(() => {
    clearAdminKey();
  });

  it("shows the login gate and unlocks with the API key", async () => {
    const user = userEvent.setup();
    renderAdmin();

    // Gate is shown; stats are NOT fetched yet.
    expect(screen.getByText("Clé API administrateur")).toBeInTheDocument();
    expect(fetchPurgeStats).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Clé API administrateur"), "secret-key");
    await user.click(screen.getByRole("button", { name: /Déverrouiller/i }));

    // Now stats load and the dashboard appears.
    await screen.findByRole("button", { name: /Purger maintenant/i });
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(fetchPurgeStats).toHaveBeenCalled();
  });

  it("masks the API key input as a password field", () => {
    renderAdmin();
    const input = screen.getByLabelText("Clé API administrateur");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
  });

  it("does not persist the key to localStorage", async () => {
    const user = userEvent.setup();
    renderAdmin();

    const snapshot = { ...localStorage };
    await user.type(screen.getByLabelText("Clé API administrateur"), "secret-key");
    await user.click(screen.getByRole("button", { name: /Déverrouiller/i }));
    await screen.findByRole("button", { name: /Purger maintenant/i });

    // The key must not be written to any storage (in-memory only).
    const stored = JSON.stringify(localStorage);
    expect(stored).not.toContain("secret-key");
    expect(Object.keys(localStorage)).toEqual(Object.keys(snapshot));
  });

  it("renders TTL config, totals and the purge button", async () => {
    setAdminKey("secret-key");
    renderAdmin();
    expect(screen.getByText("Administration")).toBeInTheDocument();
    // Wait for the stats query to resolve.
    await screen.findByRole("button", { name: /Purger maintenant/i });
    // TTL cards (values render as plain text; units are in a nested span)
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.getAllByText("heures")).toHaveLength(1);
    // Totals
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows an invalid-key error and lets the user retry", async () => {
    vi.mocked(fetchPurgeStats).mockRejectedValue(
      Object.assign(new Error("Invalid admin API key."), { response: { status: 401 } })
    );
    const user = userEvent.setup();
    renderAdmin();

    await user.type(screen.getByLabelText("Clé API administrateur"), "wrong");
    await user.click(screen.getByRole("button", { name: /Déverrouiller/i }));

    expect(await screen.findByText("Clé API invalide.")).toBeInTheDocument();
    // Retry goes back to the login gate.
    await user.click(screen.getByRole("button", { name: /Réessayer/i }));
    expect(screen.getByLabelText("Clé API administrateur")).toBeInTheDocument();
  });

  it("lists recent runs with deleted counts", async () => {
    setAdminKey("secret-key");
    renderAdmin();
    expect(await screen.findByText("Historique récent")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("1 purgé(s)")).toBeInTheDocument();
    expect(screen.getByText("0 purgé(s)")).toBeInTheDocument();
  });

  it("triggers a purge and refreshes stats", async () => {
    const user = userEvent.setup();
    setAdminKey("secret-key");
    renderAdmin();

    await user.click(
      await screen.findByRole("button", { name: /Purger maintenant/i })
    );

    expect(runPurgeNow).toHaveBeenCalledTimes(1);
    // Invalidation triggers a refetch of the stats query.
    await waitFor(() => expect(fetchPurgeStats).toHaveBeenCalledTimes(2));
    // Success feedback shown.
    expect(screen.getByText("✓ 1 signalement purgé")).toBeInTheDocument();
  });

  it("shows an error message when the purge fails", async () => {
    vi.mocked(runPurgeNow).mockRejectedValue(new Error("boom"));
    const user = userEvent.setup();
    setAdminKey("secret-key");
    renderAdmin();

    await user.click(
      await screen.findByRole("button", { name: /Purger maintenant/i })
    );

    expect(await screen.findByText("Erreur")).toBeInTheDocument();
  });
});

import { describe, expect, it, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminPage from "./AdminPage";
import { clearAdminKey, fetchAdminConfig } from "../services/admin";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "admin.pageTitle": "Administration",
        "admin.pageSubtitle": "Configuration & maintenance",
        "admin.backToDashboard": "Retour au tableau de bord",
        "admin.apiKeyLabel": "Clé API administrateur",
        "admin.unlock": "Déverrouiller",
        "admin.config.title": "Configuration du tableau de bord",
      })[key] ?? key,
  }),
}));

vi.mock("../services/admin", () => {
  let key: string | null = null;
  return {
    fetchPurgeStats: vi.fn(),
    runPurgeNow: vi.fn(),
    fetchAdminConfig: vi.fn(),
    updateAdminConfig: vi.fn(),
    setAdminKey: vi.fn((k: string) => {
      key = k.trim() || null;
    }),
    getAdminKey: vi.fn(() => key),
    clearAdminKey: vi.fn(() => {
      key = null;
    }),
  };
});

vi.mock("../components/sidebar/AdminTab", async () => {
  const admin = await vi.importActual<typeof import("../services/admin")>(
    "../services/admin"
  );
  return {
    default: ({ onUnlocked }: { onUnlocked?: () => void }) => (
      <div data-testid="admin-tab">
        <button
          type="button"
          onClick={() => {
            admin.setAdminKey("secret");
            onUnlocked?.();
          }}
        >
          unlock
        </button>
      </div>
    ),
  };
});

function renderAdmin() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AdminPage />
    </QueryClientProvider>
  );
}

describe("AdminPage", () => {
  afterEach(() => {
    clearAdminKey();
    vi.clearAllMocks();
  });

  it("renders the admin header with a back-to-dashboard link", () => {
    renderAdmin();
    expect(screen.getByText("Administration")).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Retour au tableau de bord/i });
    expect(back).toHaveAttribute("href", "/");
  });

  it("does not show the config editor before unlock", () => {
    renderAdmin();
    expect(screen.queryByText("Configuration du tableau de bord")).not.toBeInTheDocument();
  });

  it("shows the config editor once the admin key is accepted", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchAdminConfig).mockResolvedValue({});
    renderAdmin();

    await user.click(screen.getByRole("button", { name: /unlock/i }));

    expect(await screen.findByText("Configuration du tableau de bord")).toBeInTheDocument();
    expect(fetchAdminConfig).toHaveBeenCalled();
  });
});

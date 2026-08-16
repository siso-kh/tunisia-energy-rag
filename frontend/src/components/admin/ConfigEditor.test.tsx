import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfigEditor from "./ConfigEditor";
import { fetchAdminConfig, updateAdminConfig } from "../../services/admin";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "common.loading": "Chargement…",
        "admin.config.title": "Configuration du tableau de bord",
        "admin.config.subtitle": "Runtime",
        "admin.config.ttl": "Durée de vie (heures)",
        "admin.config.ttlHint": "Hint TTL",
        "admin.config.interval": "Intervalle (minutes)",
        "admin.config.intervalHint": "Hint intervalle",
        "admin.config.mapRefresh": "Actualisation (secondes)",
        "admin.config.mapRefreshHint": "Hint map",
        "admin.config.save": "Enregistrer",
        "admin.config.saved": "Configuration enregistrée !",
      })[key] ?? key,
  }),
}));

vi.mock("../../services/admin", () => ({
  fetchAdminConfig: vi.fn(),
  updateAdminConfig: vi.fn(),
}));

const CONFIG = {
  outage_ttl_hours: "5",
  outage_purge_interval_minutes: "30",
  map_refresh_seconds: "60",
};

describe("ConfigEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchAdminConfig).mockResolvedValue(CONFIG);
    vi.mocked(updateAdminConfig).mockResolvedValue({ ...CONFIG, outage_ttl_hours: "8" });
  });

  it("loads and displays the current settings", async () => {
    render(<ConfigEditor />);

    expect(await screen.findByText("Configuration du tableau de bord")).toBeInTheDocument();
    // Number inputs report numeric values.
    expect(screen.getByLabelText("Durée de vie (heures)")).toHaveValue(5);
    expect(screen.getByLabelText("Intervalle (minutes)")).toHaveValue(30);
    expect(screen.getByLabelText("Actualisation (secondes)")).toHaveValue(60);
  });

  it("saves edited values and shows confirmation", async () => {
    const user = userEvent.setup();
    render(<ConfigEditor />);

    const ttl = await screen.findByLabelText("Durée de vie (heures)");
    await user.clear(ttl);
    await user.type(ttl, "8");
    await user.click(screen.getByRole("button", { name: /Enregistrer/i }));

    expect(updateAdminConfig).toHaveBeenCalledWith({
      outage_ttl_hours: "8",
      outage_purge_interval_minutes: "30",
      map_refresh_seconds: "60",
    });
    expect(await screen.findByText("Configuration enregistrée !")).toBeInTheDocument();
  });

  it("surfaces an error when saving fails", async () => {
    vi.mocked(updateAdminConfig).mockRejectedValue(new Error("Unknown settings: foo"));
    const user = userEvent.setup();
    render(<ConfigEditor />);

    await user.click(
      await screen.findByRole("button", { name: /Enregistrer/i })
    );

    await waitFor(() =>
      expect(screen.getByText("Unknown settings: foo")).toBeInTheDocument()
    );
  });
});

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReportForm from "./ReportForm";
import { createOutage } from "../../services/outages";
import { TUNISIA_GOVERNORATES } from "../../lib/governorates";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "map.reportTitle": "Signaler une panne",
        "map.utility": "Réseau",
        "map.region": "Gouvernorat",
        "map.selectRegion": "— Choisir un gouvernorat —",
        "map.description": "Description (optionnel)",
        "map.clickMap": "Cliquez sur la carte pour placer le marqueur",
        "map.submit": "Envoyer le signalement",
        "map.submitted": "Signalement envoyé !",
        "map.steg": "Électricité (STEG)",
        "map.sonede": "Eau (SONEDE)",
        "map.other": "Autre",
        "chat.error": "Une erreur est survenue.",
      })[key] ?? key,
  }),
}));

vi.mock("../../services/outages", () => ({
  createOutage: vi.fn(),
}));

const { mutateMock, invalidateMock } = vi.hoisted(() => ({
  mutateMock: vi.fn(),
  invalidateMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: invalidateMock }),
  useMutation: (options: {
    mutationFn: (payload: unknown) => Promise<unknown>;
    onSuccess?: (data: unknown) => void;
  }) => ({
    mutate: (payload: unknown) => {
      mutateMock(payload);
      options.mutationFn(payload).then((data) => options.onSuccess?.(data));
    },
    isPending: false,
    isError: false,
  }),
}));

describe("ReportForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createOutage).mockResolvedValue({} as never);
  });

  it("offers all 24 Tunisian governorates plus a placeholder", () => {
    render(<ReportForm picked={null} />);

    const select = screen.getByLabelText("Gouvernorat") as HTMLSelectElement;
    // 24 governorates + the disabled placeholder option
    expect(select.options).toHaveLength(25);
    for (const g of TUNISIA_GOVERNORATES) {
      expect(screen.getByRole("option", { name: g })).toBeInTheDocument();
    }
  });

  it("submits without a map click (coordinates default to governorate center)", async () => {
    const user = userEvent.setup();
    render(<ReportForm picked={null} />);

    await user.selectOptions(screen.getByLabelText("Gouvernorat"), "Sfax");
    await user.click(screen.getByRole("button", { name: "Envoyer le signalement" }));

    expect(createOutage).toHaveBeenCalledWith(
      expect.objectContaining({
        region: "Sfax",
        latitude: null,
        longitude: null,
      })
    );
  });

  it("blocks submission until a governorate is selected", async () => {
    const user = userEvent.setup();
    render(<ReportForm picked={{ lat: 36.8065, lng: 10.1815 }} />);

    await user.click(screen.getByRole("button", { name: "Envoyer le signalement" }));

    expect(createOutage).not.toHaveBeenCalled();
  });

  it("submits the report with the selected governorate", async () => {
    const user = userEvent.setup();
    render(<ReportForm picked={{ lat: 36.8065, lng: 10.1815 }} />);

    await user.selectOptions(screen.getByLabelText("Gouvernorat"), "Sfax");
    await user.click(screen.getByRole("button", { name: "Envoyer le signalement" }));

    expect(mutateMock).toHaveBeenCalledWith({
      utility: "STEG",
      region: "Sfax",
      latitude: 36.8065,
      longitude: 10.1815,
      description: null,
    });
  });

  it("resets the selection and shows a confirmation on success", async () => {
    const user = userEvent.setup();
    render(<ReportForm picked={{ lat: 34.7406, lng: 10.7603 }} />);

    await user.selectOptions(screen.getByLabelText("Gouvernorat"), "Sfax");
    await user.click(screen.getByRole("button", { name: "Envoyer le signalement" }));

    expect(await screen.findByText(/Signalement envoyé !/)).toBeInTheDocument();
    await waitFor(() =>
      expect((screen.getByLabelText("Gouvernorat") as HTMLSelectElement).value).toBe("")
    );
    expect(invalidateMock).toHaveBeenCalledWith({ queryKey: ["outages"] });
  });
});

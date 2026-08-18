import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DocumentUpload from "./DocumentUpload";
import {
  DocumentIngestResult,
  ingestDocumentFromUrl,
  uploadDocument,
} from "../../services/admin";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { file?: string }) => {
      const map: Record<string, string> = {
        "admin.docs.title": "Ajouter un document",
        "admin.docs.subtitle": "Téléversez un PDF ou fournissez un lien",
        "admin.docs.uploadFile": "Fichier",
        "admin.docs.ingestUrl": "Lien",
        "admin.docs.uploadAction": "Analyser le fichier",
        "admin.docs.ingestAction": "Analyser le lien",
        "admin.docs.processing": "Analyse en cours…",
        "admin.docs.destination": "Destination",
        "admin.docs.filtered": "data/filtered/ (indexé)",
        "admin.docs.blacklisted": "data/blacklisted/",
        "admin.docs.gate1": "Gate 1",
        "admin.docs.gate2": "Gate 2",
        "admin.docs.master": "Score final",
        "admin.docs.pages": "Pages",
        "admin.docs.chunks": "Chunks indexés",
        "admin.docs.indexError": "Erreur d'indexation",
      };
      if (key === "admin.docs.accepted") return `✓ Accepté : ${opts?.file}`;
      if (key === "admin.docs.rejected") return `✗ Rejeté : ${opts?.file}`;
      return map[key] ?? key;
    },
  }),
}));

vi.mock("../../services/admin", () => ({
  uploadDocument: vi.fn(),
  ingestDocumentFromUrl: vi.fn(),
}));

const ACCEPTED: DocumentIngestResult = {
  filename: "guide.pdf",
  status: "PASSED",
  dest: "filtered",
  gate1_score: 92,
  gate2_score: 88,
  master_score: 90,
  total_pages: 5,
  chunks_indexed: 7,
};

const REJECTED: DocumentIngestResult = {
  filename: "doc.pdf",
  status: "BLACKLISTED",
  dest: "blacklisted",
  gate1_score: 10,
  gate2_score: 5,
  master_score: 6,
  total_pages: 3,
  chunks_indexed: 0,
};

describe("DocumentUpload", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uploads a selected PDF file and shows the accepted result", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(ACCEPTED);
    const user = userEvent.setup();
    render(<DocumentUpload />);

    const file = new File(["%PDF-1.4"], "guide.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Fichier"), file);
    await user.click(screen.getByRole("button", { name: /Analyser le fichier/i }));

    expect(uploadDocument).toHaveBeenCalledWith(file);
    expect(await screen.findByTestId("doc-result")).toBeInTheDocument();
    expect(screen.getByText("✓ Accepté : guide.pdf")).toBeInTheDocument();
    expect(screen.getByText(/data\/filtered\/ \(indexé\)/)).toBeInTheDocument();
    expect(screen.getByText(/Chunks indexés: 7/)).toBeInTheDocument();
  });

  it("disables the upload button until a file is chosen", async () => {
    const user = userEvent.setup();
    render(<DocumentUpload />);

    const btn = screen.getByRole("button", { name: /Analyser le fichier/i });
    expect(btn).toBeDisabled();

    const file = new File(["%PDF-1.4"], "a.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Fichier"), file);
    expect(btn).toBeEnabled();
  });

  it("shows the rejected result for a blacklisted document", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(REJECTED);
    const user = userEvent.setup();
    render(<DocumentUpload />);

    const file = new File(["%PDF-1.4"], "doc.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Fichier"), file);
    await user.click(screen.getByRole("button", { name: /Analyser le fichier/i }));

    expect(await screen.findByText("✗ Rejeté : doc.pdf")).toBeInTheDocument();
    expect(screen.getByText(/data\/blacklisted\//)).toBeInTheDocument();
  });

  it("surfaces the backend error message on failure", async () => {
    vi.mocked(uploadDocument).mockRejectedValue(new Error("Uploaded file is not a valid PDF."));
    const user = userEvent.setup();
    render(<DocumentUpload />);

    const file = new File(["not a pdf"], "doc.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Fichier"), file);
    await user.click(screen.getByRole("button", { name: /Analyser le fichier/i }));

    expect(await screen.findByText("Uploaded file is not a valid PDF.")).toBeInTheDocument();
  });

  it("ingests from a URL and shows the result", async () => {
    vi.mocked(ingestDocumentFromUrl).mockResolvedValue(ACCEPTED);
    const user = userEvent.setup();
    render(<DocumentUpload />);

    await user.click(screen.getByRole("button", { name: "Lien" }));
    await user.type(
      screen.getByLabelText("Lien"),
      "https://example.org/rapport.pdf"
    );
    await user.click(screen.getByRole("button", { name: /Analyser le lien/i }));

    expect(ingestDocumentFromUrl).toHaveBeenCalledWith("https://example.org/rapport.pdf");
    expect(await screen.findByTestId("doc-result")).toBeInTheDocument();
  });
});

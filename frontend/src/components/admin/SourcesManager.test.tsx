import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SourcesManager from "./SourcesManager";
import {
  addSource,
  deleteSource,
  fetchSources,
  ingestSourcesStream,
  researchSourcesStream,
  Source,
} from "../../services/admin";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => {
      const map: Record<string, string> = {
        "admin.sources.title": "Gestion des sources",
        "admin.sources.subtitle": "Ajoutez des URLs PDF",
        "admin.sources.addPlaceholder": "https://example.org/rapport.pdf",
        "admin.sources.addButton": "Ajouter",
        "admin.sources.researchButton": "Recherche approfondie",
        "admin.sources.ingestButton": "Indexer dans ChromaDB",
        "admin.sources.researching": "Recherche en cours…",
        "admin.sources.ingesting": "Indexation en cours…",
        "admin.sources.url": "URL",
        "admin.sources.file": "Fichier",
        "admin.sources.statusLabel": "Statut",
        "admin.sources.size": "Taille",
        "admin.sources.score": "Score",
        "admin.sources.chunks": "Chunks",
        "admin.sources.totalSources": "source(s)",
        "admin.sources.pending": "en attente",
        "admin.sources.readyToIngest": "prêt(s)",
        "admin.sources.indexed": "indexé(s)",
        "admin.sources.delete": "Supprimer",
        "admin.sources.noSources": "Aucune source ajoutée.",
        "admin.progress.done": "Terminé",
        "common.loading": "Chargement…",
        "admin.sources.statusLabels.pending": "En attente",
        "admin.sources.statusLabels.downloading": "Téléchargement…",
        "admin.sources.statusLabels.downloaded": "Téléchargé",
        "admin.sources.statusLabels.failed": "Échoué",
        "admin.sources.statusLabels.ingesting": "Indexation…",
        "admin.sources.statusLabels.indexed": "Indexé",
        "admin.sources.statusLabels.triage_rejected": "Rejeté",
      };
      if (key === "admin.sources.researchDone")
        return `Recherche: ${opts?.downloaded} téléchargé(s), ${opts?.failed} échoué(s)`;
      if (key === "admin.sources.ingestDone")
        return `Indexation: ${opts?.indexed} indexé(s), ${opts?.rejected} rejeté(s)`;
      return map[key] ?? key;
    },
  }),
}));

vi.mock("../../services/admin", () => ({
  fetchSources: vi.fn(),
  addSource: vi.fn(),
  deleteSource: vi.fn(),
  crawlWebsite: vi.fn(),
  researchSourcesStream: vi.fn(),
  ingestSourcesStream: vi.fn(),
}));

const PENDING_SOURCE: Source = {
  id: "s1",
  url: "https://example.org/doc.pdf",
  filename: null,
  status: "pending",
  file_size: null,
  total_pages: null,
  gate1_score: null,
  master_score: null,
  chunks_indexed: null,
  error_message: null,
  created_at: "2026-08-18T10:00:00Z",
  updated_at: "2026-08-18T10:00:00Z",
};

describe("SourcesManager", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads and displays sources in the table", async () => {
    const downloaded = {
      ...PENDING_SOURCE,
      id: "s2",
      url: "https://example.org/guide.pdf",
      status: "downloaded" as const,
      filename: "guide.pdf",
    };
    vi.mocked(fetchSources).mockResolvedValue([PENDING_SOURCE, downloaded]);
    render(<SourcesManager />);

    expect(await screen.findByText("example.org/doc.pdf")).toBeInTheDocument();
    expect(screen.getByText("guide.pdf")).toBeInTheDocument();
    expect(screen.getByText("2 source(s)")).toBeInTheDocument();
  });

  it("shows empty state when no sources exist", async () => {
    vi.mocked(fetchSources).mockResolvedValue([]);
    render(<SourcesManager />);

    expect(await screen.findByText("Aucune source ajoutée.")).toBeInTheDocument();
  });

  it("adds a source on form submit", async () => {
    vi.mocked(fetchSources).mockResolvedValue([]);
    vi.mocked(addSource).mockResolvedValue(PENDING_SOURCE);
    const user = userEvent.setup();
    render(<SourcesManager />);

    await screen.findByText("Aucune source ajoutée.");
    await user.type(
      screen.getByPlaceholderText("https://example.org/rapport.pdf"),
      "https://example.org/new.pdf"
    );
    await user.click(screen.getByRole("button", { name: /Ajouter/i }));

    expect(addSource).toHaveBeenCalledWith("https://example.org/new.pdf");
  });

  it("calls deleteSource when delete button clicked", async () => {
    vi.mocked(fetchSources).mockResolvedValue([PENDING_SOURCE]);
    vi.mocked(deleteSource).mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SourcesManager />);

    // PENDING_SOURCE has no filename, so look for the URL domain instead.
    await screen.findByText("example.org/doc.pdf");
    const deleteBtn = screen.getByTitle("Supprimer");
    await user.click(deleteBtn);

    expect(deleteSource).toHaveBeenCalledWith("s1");
  });

  it("calls researchSourcesStream when research button clicked", async () => {
    vi.mocked(fetchSources).mockResolvedValue([PENDING_SOURCE]);
    vi.mocked(researchSourcesStream).mockImplementation(async (onProgress) => {
      onProgress({ type: "progress", current: 1, total: 1, status: "downloading", filename: "doc.pdf" });
      onProgress({ type: "file_done", current: 1, total: 1, status: "downloaded", filename: "doc.pdf" });
      return { type: "done", downloaded: 1, failed: 0, total: 1 };
    });
    const user = userEvent.setup();
    render(<SourcesManager />);

    await screen.findByText("example.org/doc.pdf");
    await user.click(screen.getByRole("button", { name: /Recherche approfondie/i }));

    expect(researchSourcesStream).toHaveBeenCalled();
  });

  it("calls ingestSourcesStream when ingest button clicked", async () => {
    const downloadedSource = { ...PENDING_SOURCE, status: "downloaded" as const, filename: "doc.pdf" };
    vi.mocked(fetchSources).mockResolvedValue([downloadedSource]);
    vi.mocked(ingestSourcesStream).mockImplementation(async (onProgress) => {
      onProgress({ type: "progress", current: 1, total: 1, status: "ingesting", filename: "doc.pdf" });
      onProgress({ type: "file_done", current: 1, total: 1, status: "indexed", filename: "doc.pdf" });
      return { type: "done", indexed: 1, rejected: 0, failed: 0, total: 1 };
    });
    const user = userEvent.setup();
    render(<SourcesManager />);

    await screen.findByText("doc.pdf");
    await user.click(screen.getByRole("button", { name: /Indexer dans ChromaDB/i }));

    expect(ingestSourcesStream).toHaveBeenCalled();
  });

  it("disables research button when no pending sources", async () => {
    // Use a downloaded source (not pending) — research button should be disabled.
    const downloadedSource = { ...PENDING_SOURCE, status: "downloaded" as const, filename: "doc.pdf" };
    vi.mocked(fetchSources).mockResolvedValue([downloadedSource]);
    render(<SourcesManager />);

    await screen.findByText("doc.pdf");
    const btn = screen.getByRole("button", { name: /Recherche approfondie/i });
    expect(btn).toBeDisabled();
  });

  it("disables ingest button when no downloaded sources", async () => {
    vi.mocked(fetchSources).mockResolvedValue([PENDING_SOURCE]);
    render(<SourcesManager />);

    await screen.findByText("example.org/doc.pdf");
    const btn = screen.getByRole("button", { name: /Indexer dans ChromaDB/i });
    expect(btn).toBeDisabled();
  });
});

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatInput from "./ChatInput";
import MessageBubble from "./MessageBubble";

// react-i18next is exercised end-to-end elsewhere; stub it here to keep
// component tests focused on behaviour.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "chat.placeholder": "Posez votre question ici…",
        "chat.send": "Envoyer",
        "chat.sources": "Sources consultées",
        "chat.copySources": "Copier les sources",
        "chat.copied": "Copié !",
        "chat.viewMore": "Voir plus",
        "chat.viewLess": "Réduire",
      })[key] ?? key,
  }),
}));

describe("ChatInput", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls onSend with the trimmed query and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    const onCancel = vi.fn();

    render(<ChatInput onSend={onSend} onCancel={onCancel} busy={false} />);

    const input = screen.getByLabelText("Posez votre question ici…");
    await user.type(input, "  Qui est la STEG ?  ");
    await user.click(screen.getByRole("button", { name: /envoyer/i }));

    expect(onSend).toHaveBeenCalledWith("Qui est la STEG ?");
    expect(input).toHaveValue("");
  });

  it("does not send empty or whitespace-only input", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();

    render(<ChatInput onSend={onSend} onCancel={vi.fn()} busy={false} />);

    const input = screen.getByLabelText("Posez votre question ici…");
    await user.type(input, "   ");
    await user.click(screen.getByRole("button", { name: /envoyer/i }));

    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows a cancel button instead of send while busy", () => {
    render(<ChatInput onSend={vi.fn()} onCancel={vi.fn()} busy={true} />);

    expect(screen.queryByRole("button", { name: /envoyer/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "⏹" })).toBeInTheDocument();
  });
});

describe("MessageBubble", () => {
  it("renders user messages right-aligned without citations", () => {
    render(<MessageBubble message={{ role: "user", content: "Bonjour" }} />);

    expect(screen.getByText("Bonjour")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders markdown in assistant messages", () => {
    render(
      <MessageBubble
        message={{ role: "assistant", content: "**L'ANME** gère la politique." }}
      />
    );

    const strong = screen.getByText("L'ANME");
    expect(strong.tagName).toBe("STRONG");
  });

  it("shows a collapsible sources dropdown grouping chunks by file", async () => {
    const user = userEvent.setup();
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Réponse",
          sources: [
            { source_file: "anme.pdf", page: 3, content: "extrait A" },
            { source_file: "anme.pdf", page: 4, content: "extrait B" },
            { source_file: "steg.pdf", page: 7, content: "autre" },
          ],
        }}
      />
    );

    // Collapsed: only the toggle (with the count) is visible.
    const toggle = screen.getByRole("button", { name: /Sources consultées \(3\)/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("anme.pdf")).not.toBeInTheDocument();

    // Expand: file names, pages and content appear.
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("anme.pdf")).toBeInTheDocument();
    expect(screen.getByText("steg.pdf")).toBeInTheDocument();
    expect(screen.getByText("p.3")).toBeInTheDocument();
    expect(screen.getByText("p.4")).toBeInTheDocument();
    expect(screen.getByText("p.7")).toBeInTheDocument();
  });

  it("renders the publication date parsed from the filename", async () => {
    const user = userEvent.setup();
    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Réponse",
          sources: [{ source_file: "IRENA_June-2014.pdf", page: 2, content: "extrait", date: "Juin 2014" }],
        }}
      />
    );

    await user.click(screen.getByRole("button", { name: /Sources consultées \(1\)/i }));
    expect(screen.getByText("Juin 2014")).toBeInTheDocument();
  });

  it("copies the formatted source list to the clipboard and shows feedback", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    // jsdom has no clipboard by default; install a stub.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Réponse",
          sources: [
            { source_file: "anme.pdf", page: 3, content: "extrait A", date: "2026" },
            { source_file: "steg.pdf", page: 7, content: "autre" },
          ],
        }}
      />
    );

    await user.click(screen.getByRole("button", { name: /Sources consultées \(2\)/i }));
    await user.click(screen.getByRole("button", { name: /Copier les sources/i }));

    expect(writeText).toHaveBeenCalledWith(
      "📄 anme.pdf (2026)\n   p.3 — extrait A\n\n📄 steg.pdf\n   p.7 — autre"
    );
    expect(screen.getByRole("button", { name: /Copié !/i })).toBeInTheDocument();
  });

  it("expands a long chunk to its full text and collapses it back", async () => {
    const user = userEvent.setup();
    const longContent = `Lorem ipsum dolor sit amet, consectetur adipiscing elit. ${`phrase répétée très longue pour dépasser la limite d'affichage. `.repeat(8)}`.trim();

    render(
      <MessageBubble
        message={{
          role: "assistant",
          content: "Réponse",
          sources: [{ source_file: "anme.pdf", page: 3, content: longContent }],
        }}
      />
    );

    await user.click(screen.getByRole("button", { name: /Sources consultées \(1\)/i }));

    const expand = screen.getByRole("button", { name: /Voir plus/i });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText(longContent)).toHaveClass("line-clamp-3");

    await user.click(expand);
    expect(expand).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(longContent)).not.toHaveClass("line-clamp-3");
    expect(screen.getByRole("button", { name: /Réduire/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Réduire/i }));
    expect(screen.getByText(longContent)).toHaveClass("line-clamp-3");
  });

  it("does not render a sources dropdown when there are no sources", () => {
    render(<MessageBubble message={{ role: "assistant", content: "Réponse" }} />);
    expect(screen.queryByTestId("sources-dropdown")).not.toBeInTheDocument();
  });

  it("shows a streaming cursor when streaming", () => {
    render(
      <MessageBubble
        message={{ role: "assistant", content: "En cours" }}
        streaming
      />
    );
    expect(screen.getByTestId("streaming-cursor")).toBeInTheDocument();
  });
});

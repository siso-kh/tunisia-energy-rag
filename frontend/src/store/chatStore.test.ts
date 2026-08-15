import { describe, it, expect, beforeEach } from "vitest";
import { useChatStore } from "./chatStore";

describe("chatStore", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("starts empty and idle", () => {
    const state = useChatStore.getState();
    expect(state.messages).toEqual([]);
    expect(state.phase).toBe("idle");
    expect(state.conversationId).toBeNull();
    expect(state.latestSources).toEqual([]);
  });

  it("addUserMessage appends a user message and enters contextualizing", () => {
    const { addUserMessage } = useChatStore.getState();
    addUserMessage("Qui est l'ANME ?");

    const state = useChatStore.getState();
    expect(state.messages).toEqual([{ role: "user", content: "Qui est l'ANME ?" }]);
    expect(state.phase).toBe("contextualizing");
  });

  it("appendToken creates an assistant bubble on first token", () => {
    useChatStore.getState().addUserMessage("Question");
    useChatStore.getState().appendToken("Bon");

    let state = useChatStore.getState();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[1]).toEqual({ role: "assistant", content: "Bon" });

    // subsequent tokens accumulate on the same assistant message
    useChatStore.getState().appendToken("jour");
    state = useChatStore.getState();
    expect(state.messages[1].content).toBe("Bonjour");
  });

  it("setSources stores the retrieved citations", () => {
    const sources = [{ source_file: "doc.pdf", page: 3, content: "extrait" }];
    useChatStore.getState().setSources(sources);
    expect(useChatStore.getState().latestSources).toEqual(sources);
  });

  it("finalizeAnswer attaches sources to the streamed assistant message", () => {
    const sources = [{ source_file: "anme.pdf", page: 1, content: "c" }];
    useChatStore.getState().addUserMessage("Q");
    useChatStore.getState().appendToken("Reponse partielle");
    useChatStore.getState().finalizeAnswer("Reponse partielle", sources, "conv-1");

    const state = useChatStore.getState();
    expect(state.messages[1].content).toBe("Reponse partielle");
    expect(state.messages[1].sources).toEqual(sources);
    expect(state.conversationId).toBe("conv-1");
    expect(state.phase).toBe("idle");
    expect(state.latestSources).toEqual(sources);
  });

  it("finalizeAnswer with empty stream pushes a fresh assistant message", () => {
    useChatStore.getState().addUserMessage("Q");
    useChatStore.getState().finalizeAnswer("", [], "conv-2");

    const state = useChatStore.getState();
    expect(state.messages).toHaveLength(2);
    expect(state.messages[1]).toEqual({ role: "assistant", content: "", sources: [] });
  });

  it("finalizeAnswer keeps the existing conversation id when none provided", () => {
    useChatStore.setState({ conversationId: "existing-id" });
    useChatStore.getState().addUserMessage("Q");
    useChatStore.getState().finalizeAnswer("A", []);

    expect(useChatStore.getState().conversationId).toBe("existing-id");
  });

  it("setPhase transitions the streaming phase", () => {
    useChatStore.getState().setPhase("searching");
    expect(useChatStore.getState().phase).toBe("searching");
    useChatStore.getState().setPhase("error");
    expect(useChatStore.getState().phase).toBe("error");
  });

  it("reset clears messages, conversation, phase and sources", () => {
    useChatStore.setState({
      messages: [{ role: "user", content: "x" }],
      conversationId: "c1",
      phase: "generating",
      latestSources: [{ source_file: "a.pdf", page: 1, content: "c" }],
    });
    useChatStore.getState().reset();

    const state = useChatStore.getState();
    expect(state.messages).toEqual([]);
    expect(state.conversationId).toBeNull();
    expect(state.phase).toBe("idle");
    expect(state.latestSources).toEqual([]);
  });
});

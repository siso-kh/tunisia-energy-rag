import { create } from "zustand";
import type { ChatMessage, Source } from "../types";

export type StreamPhase = "idle" | "contextualizing" | "searching" | "generating" | "error";

interface ChatState {
  messages: ChatMessage[];
  conversationId: string | null;
  phase: StreamPhase;
  latestSources: Source[];
  addUserMessage: (content: string) => void;
  setSources: (sources: Source[]) => void;
  appendToken: (content: string) => void;
  finalizeAnswer: (answer: string, sources: Source[], conversationId?: string | null) => void;
  setPhase: (phase: StreamPhase) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  conversationId: null,
  phase: "idle",
  latestSources: [],

  addUserMessage: (content) =>
    set((state) => ({
      messages: [...state.messages, { role: "user", content }],
      phase: "contextualizing",
      latestSources: [],
    })),

  setSources: (sources) => set({ latestSources: sources }),

  appendToken: (content) =>
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant") {
        messages[messages.length - 1] = { ...last, content: last.content + content };
      } else {
        messages.push({ role: "assistant", content });
      }
      return { messages };
    }),

  finalizeAnswer: (answer, sources, conversationId) =>
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last && last.role === "assistant" && last.content.length > 0) {
        messages[messages.length - 1] = { ...last, sources };
      } else {
        messages.push({ role: "assistant", content: answer, sources });
      }
      return {
        messages,
        latestSources: sources,
        conversationId: conversationId ?? state.conversationId,
        phase: "idle",
      };
    }),

  setPhase: (phase) => set({ phase }),

  reset: () =>
    set({ messages: [], conversationId: null, phase: "idle", latestSources: [] }),
}));

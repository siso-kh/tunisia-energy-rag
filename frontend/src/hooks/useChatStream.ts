import { useCallback, useRef } from "react";
import { streamChat } from "../services/chat";
import { useChatStore, StreamPhase } from "../store/chatStore";
import type { StreamEvent } from "../types";

// Map backend status strings to store phases (identical today, but isolated
// so a backend wording change can't silently type-mismatch).
const STATUS_TO_PHASE: Record<string, StreamPhase> = {
  contextualizing: "contextualizing",
  searching: "searching",
  generating: "generating",
};

const handleEvent = (event: StreamEvent) => {
  const s = useChatStore.getState();
  switch (event.type) {
    case "status":
      s.setPhase(STATUS_TO_PHASE[event.message] ?? "searching");
      break;
    case "sources":
      s.setSources(event.sources);
      break;
    case "token":
      s.appendToken(event.content);
      break;
    case "done":
      s.finalizeAnswer(event.answer, event.sources, event.conversation_id);
      break;
    case "error":
      s.setPhase("error");
      break;
  }
};

/**
 * Drives a streaming chat turn: wires the store to /api/chat/stream events
 * and supports cancellation via AbortController.
 */
export function useChatStream() {
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(async (query: string) => {
    // Capture the history BEFORE adding the new message: this is exactly what
    // gets sent to the backend as chat_history (the new query is separate).
    const history = useChatStore.getState().messages;
    useChatStore.getState().addUserMessage(query);

    const controller = new AbortController();
    abortRef.current = controller;

    const payload = {
      query,
      chat_history: history.map((m) => ({ role: m.role, content: m.content })),
      conversation_id: useChatStore.getState().conversationId,
    };

    let finished = false;

    try {
      await streamChat(
        payload,
        (event) => {
          if (event.type === "done" || event.type === "error") finished = true;
          handleEvent(event);
        },
        controller.signal
      );
      // Stream ended without a terminal event (connection dropped): don't
      // leave the UI stuck in "generating".
      if (!finished && useChatStore.getState().phase === "generating") {
        useChatStore.getState().setPhase("idle");
      }
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        useChatStore.getState().setPhase("idle");
      } else {
        useChatStore.getState().setPhase("error");
      }
    } finally {
      abortRef.current = null;
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { sendMessage, cancel };
}

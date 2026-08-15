import api from "./api";
import type {
  ChatMessage,
  QueryResponse,
  StreamEvent,
} from "../types";

export interface ChatPayload {
  query: string;
  chat_history: ChatMessage[];
  conversation_id?: string | null;
}

/** Non-streaming fallback: plain JSON response from /api/chat. */
export async function fetchChatAnswer(payload: ChatPayload): Promise<QueryResponse> {
  const { data } = await api.post<QueryResponse>("/chat", payload);
  return data;
}

/**
 * Streaming chat via fetch + ReadableStream.
 *
 * Native EventSource only supports GET, so we use fetch with a POST body and
 * parse the SSE frames manually. Each frame looks like: "data: {json}\n\n".
 * Supports cancellation via AbortController.
 */
export async function streamChat(
  payload: ChatPayload,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok || !response.body) {
    let detail = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      if (data?.detail) detail = data.detail;
    } catch {
      /* keep default */
    }
    onEvent({ type: "error", message: detail });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    // Normalize CRLF to LF so frames split cleanly regardless of the wire
    // format (the backend sends LF, but proxies can rewrite line endings).
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    // SSE frames are separated by a blank line
    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!dataLine) continue;
      try {
        const event = JSON.parse(dataLine.slice(6)) as StreamEvent;
        onEvent(event);
      } catch {
        /* ignore malformed frames */
      }
    }
  }
}

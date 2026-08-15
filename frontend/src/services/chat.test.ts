import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { streamChat } from "./chat";
import type { StreamEvent } from "../types";

/** Build a Response-like object whose body streams SSE text chunks. */
function sseResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function collectEvents(chunks: string[], status = 200): Promise<StreamEvent[]> {
  const events: StreamEvent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(sseResponse(chunks, status))
  );
  return streamChat(
    { query: "test", chat_history: [] },
    (e) => events.push(e)
  ).then(() => events);
}

describe("streamChat (SSE parser)", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("parses multiple frames delivered in a single chunk", async () => {
    const body =
      'data: {"type":"status","message":"searching"}\n\n' +
      'data: {"type":"sources","sources":[]}\n\n' +
      'data: {"type":"done","answer":"Bonjour","sources":[]}\n\n';

    const events = await collectEvents([body]);

    expect(events.map((e) => e.type)).toEqual(["status", "sources", "done"]);
    expect(events[2]).toMatchObject({ type: "done", answer: "Bonjour" });
  });

  it("reassembles frames split across multiple chunks", async () => {
    // Frame boundary falls in the middle of a chunk -> must still parse once
    // the next chunk arrives.
    const chunk1 = 'data: {"type":"token","conten';
    const chunk2 = 't":"Bon"}\n\ndata: {"type":"token","content":"jour"}\n\n';

    const events = await collectEvents([chunk1, chunk2]);

    expect(events.map((e) => e.type)).toEqual(["token", "token"]);
    expect(events.map((e) => (e as any).content)).toEqual(["Bon", "jour"]);
  });

  it("splits multiple frames inside one chunk correctly", async () => {
    const chunk =
      'data: {"type":"token","content":"A"}\n\n' +
      'data: {"type":"token","content":"B"}\n\n';

    const events = await collectEvents([chunk]);
    expect(events.map((e) => (e as any).content)).toEqual(["A", "B"]);
  });

  it("ignores non-data lines (comments, event fields)", async () => {
    const body =
      ": keep-alive comment\n\n" +
      'event: custom\n' +
      'data: {"type":"token","content":"ok"}\n\n';

    const events = await collectEvents([body]);
    // only the data: frame with valid JSON is emitted
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "token", content: "ok" });
  });

  it("tolerates CRLF frame separators (proxy-rewritten wire format)", async () => {
    const body =
      'data: {"type":"status","message":"searching"}\r\n\r\n' +
      'data: {"type":"token","content":"A"}\r\n\r\n';

    const events = await collectEvents([body]);
    expect(events.map((e) => e.type)).toEqual(["status", "token"]);
  });

  it("silently skips malformed JSON frames", async () => {
    const body = 'data: {not json}\n\n' + 'data: {"type":"done","answer":"x","sources":[]}\n\n';

    const events = await collectEvents([body]);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "done" });
  });

  it("emits an error event with the backend detail on non-200", async () => {
    const errorBody = JSON.stringify({ detail: "Query cannot be empty." });
    const response = new Response(errorBody, { status: 400 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const events: StreamEvent[] = [];
    await streamChat({ query: "", chat_history: [] }, (e) => events.push(e));

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "error", message: "Query cannot be empty." });
  });

  it("falls back to the HTTP status when error body has no detail", async () => {
    const response = new Response("Internal Server Error", { status: 500 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const events: StreamEvent[] = [];
    await streamChat({ query: "q", chat_history: [] }, (e) => events.push(e));

    expect(events[0]).toMatchObject({ type: "error", message: "HTTP 500" });
  });

  it("passes the AbortSignal to fetch for cancellation", async () => {
    // NB: a bare `new ReadableStream()` never closes, so reader.read() would
    // hang forever — the stream must close immediately like the real SSE one.
    const closedStream = new ReadableStream({
      start(controller) {
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(closedStream, { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    await streamChat(
      { query: "q", chat_history: [] },
      () => {},
      controller.signal
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.signal).toBe(controller.signal);
  });

  it("propagates a mid-stream abort as a rejection for the caller to handle", async () => {
    // Simulates reader.read() rejecting with AbortError (real cancel path in
    // useChatStream, which catches it and resets the phase to idle).
    const failingStream = new ReadableStream({
      start(controller) {
        controller.error(
          new DOMException("The operation was aborted.", "AbortError")
        );
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(failingStream, { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      streamChat({ query: "q", chat_history: [] }, () => {})
    ).rejects.toThrow("The operation was aborted.");
  });

  it("posts the payload as JSON to the stream endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    const payload = {
      query: "q",
      chat_history: [{ role: "user" as const, content: "c" }],
      conversation_id: "conv-1",
    };
    await streamChat(payload, () => {});

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat/stream");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual(payload);
  });
});

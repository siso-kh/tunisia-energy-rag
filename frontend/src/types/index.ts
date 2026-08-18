// Shared API types mirroring the FastAPI contracts (src/api/main.py).

export interface Source {
  source_file: string;
  page: number | string;
  content: string;
  /** Best-effort publication date parsed from the filename (may be absent). */
  date?: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  sources?: Source[] | null;
}

export interface QueryResponse {
  query: string;
  sources: Source[];
  answer: string;
  conversation_id?: string | null;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string | null;
  messages: Array<{
    id: string;
    role: string;
    content: string;
    sources: Source[] | null;
    created_at: string;
  }>;
}

export type Utility = "STEG" | "SONEDE" | "OTHER";
export type OutageStatus = "PENDING" | "VERIFIED" | "RESOLVED";

export interface OutageReport {
  id: string;
  utility: Utility;
  region: string;
  latitude: number;
  longitude: number;
  description: string | null;
  status: OutageStatus;
  created_at: string;
}

export interface OutageCreatePayload {
  utility: Utility;
  region: string;
  latitude?: number | null;
  longitude?: number | null;
  description?: string | null;
}

// SSE stream event types emitted by /api/chat/stream
export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "sources"; sources: Source[] }
  | { type: "token"; content: string }
  | { type: "done"; answer: string; sources: Source[]; conversation_id?: string | null }
  | { type: "error"; message: string; rateLimited?: boolean };

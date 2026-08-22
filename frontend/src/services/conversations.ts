import api from "./api";
import type { ConversationSummary, ConversationDetail } from "../types";

/** List all conversations for the current user. */
export async function listConversations(): Promise<ConversationSummary[]> {
  const { data } = await api.get<ConversationSummary[]>("/conversations");
  return data;
}

/** Get a single conversation with all its messages. */
export async function getConversation(
  id: string
): Promise<ConversationDetail> {
  const { data } = await api.get<ConversationDetail>(`/conversations/${id}`);
  return data;
}

/** Create a new empty conversation. Returns the conversation ID. */
export async function createConversation(
  title?: string
): Promise<ConversationSummary> {
  const { data } = await api.post<ConversationSummary>("/conversations", {
    title: title || null,
  });
  return data;
}

/** Delete a conversation by ID. */
export async function deleteConversation(id: string): Promise<void> {
  await api.delete(`/conversations/${id}`);
}

/** Delete all conversations for the current user. */
export async function deleteAllConversations(): Promise<number> {
  const { data } = await api.delete<{ status: string; deleted: number }>("/conversations");
  return data.deleted;
}

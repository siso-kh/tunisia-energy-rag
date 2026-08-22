import { useEffect, useState, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useChatStore } from "../../store/chatStore";
import {
  listConversations,
  getConversation,
  deleteConversation,
  deleteAllConversations,
} from "../../services/conversations";
import type { ConversationSummary } from "../../types";

/**
 * Sidebar panel listing the user's previous conversations.
 * Clicking a conversation loads its messages into the chat panel.
 * Auto-refreshes when a new message is sent (conversationId changes).
 */
export default function ConversationList() {
  const { t } = useTranslation();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reset = useChatStore((s) => s.reset);
  const conversationId = useChatStore((s) => s.conversationId);
  const messages = useChatStore((s) => s.messages);

  // Track the last known message count to detect new messages
  const lastMsgCountRef = useRef(0);

  const loadConversations = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      console.log("[History] Loading conversations...");
      const data = await listConversations();
      console.log("[History] Loaded", data.length, "conversations");
      setConversations(data);
    } catch (err: unknown) {
      console.error("[History] Failed to load:", err);
      const message =
        err instanceof Error ? err.message : "Failed to load conversations";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    console.log("[History] Component mounted, loading conversations...");
    loadConversations();
  }, [loadConversations]);

  // Auto-refresh when a new message is sent (messages array grows)
  useEffect(() => {
    const currentCount = messages.length;
    if (currentCount > lastMsgCountRef.current && lastMsgCountRef.current > 0) {
      // New message was sent — refresh the list after a short delay
      // (backend needs time to persist)
      const timer = setTimeout(() => {
        loadConversations();
      }, 500);
      return () => clearTimeout(timer);
    }
    lastMsgCountRef.current = currentCount;
  }, [messages.length, loadConversations]);

  // Also refresh when conversationId changes (new conversation created)
  useEffect(() => {
    if (conversationId) {
      const timer = setTimeout(() => {
        loadConversations();
      }, 300);
      return () => clearTimeout(timer);
    }
  }, [conversationId, loadConversations]);

  const handleSelect = async (id: string) => {
    try {
      const detail = await getConversation(id);
      // Reset the store first
      useChatStore.setState({
        messages: [],
        conversationId: id,
        phase: "idle",
        latestSources: [],
        errorRateLimited: false,
      });
      // Load messages one by one
      for (const msg of detail.messages) {
        if (msg.role === "user") {
          useChatStore.getState().addUserMessage(msg.content);
        } else if (msg.role === "assistant") {
          useChatStore.getState().appendToken(msg.content);
          if (msg.sources) {
            useChatStore.getState().setSources(msg.sources);
          }
        }
      }
    } catch (err: unknown) {
      console.error("Failed to load conversation:", err);
    }
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      // If we deleted the active conversation, reset the chat
      if (conversationId === id) {
        reset();
      }
    } catch (err: unknown) {
      console.error("Failed to delete conversation:", err);
    }
  };

  const handleNew = () => {
    reset();
  };

  const handleDeleteAll = async () => {
    if (!window.confirm(t("chat.confirmDeleteAll", "Delete all conversations? This cannot be undone."))) {
      return;
    }
    try {
      await deleteAllConversations();
      setConversations([]);
      reset();
    } catch (err: unknown) {
      console.error("Failed to delete all conversations:", err);
    }
  };

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      });
    } else if (diffDays === 1) {
      return t("chat.yesterday", "Yesterday");
    } else if (diffDays < 7) {
      return t("chat.daysAgo", "{{count}} days ago", { count: diffDays });
    } else {
      return date.toLocaleDateString();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700">
        <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          {t("chat.history", "Conversations")}
        </h3>
        <div className="flex gap-1">
          <button
            onClick={loadConversations}
            className="text-xs px-2 py-1 rounded bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors"
            title={t("chat.refresh", "Refresh")}
          >
            ↻
          </button>
          <button
            onClick={handleNew}
            className="text-xs px-2 py-1 rounded bg-blue-500 text-white hover:bg-blue-600 transition-colors"
            title={t("chat.newConversation", "New conversation")}
          >
            + {t("chat.new", "New")}
          </button>
          {conversations.length > 0 && (
            <button
              onClick={handleDeleteAll}
              className="text-xs px-2 py-1 rounded bg-red-500 text-white hover:bg-red-600 transition-colors"
              title={t("chat.deleteAll", "Delete all")}
            >
              🗑
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {loading && (
          <div className="p-4 text-center text-gray-500 text-sm">
            {t("chat.loading", "Loading...")}
          </div>
        )}

        {error && (
          <div className="p-4 text-center text-red-500 text-sm">{error}</div>
        )}

        {!loading && !error && conversations.length === 0 && (
          <div className="p-4 text-center text-gray-400 text-sm">
            {t("chat.noConversations", "No conversations yet")}
          </div>
        )}

        {!loading && !error && conversations.length > 0 && (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {conversations.map((conv) => (
              <li
                key={conv.id}
                onClick={() => handleSelect(conv.id)}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors
                  ${
                    conversationId === conv.id
                      ? "bg-blue-50 dark:bg-blue-900/30"
                      : "hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`}
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm truncate text-gray-800 dark:text-gray-200">
                    {conv.title || t("chat.untitled", "Untitled conversation")}
                  </p>
                  <p className="text-xs text-gray-400">
                    {formatDate(conv.updated_at)}
                  </p>
                </div>
                <button
                  onClick={(e) => handleDelete(conv.id, e)}
                  className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-opacity p-1"
                  title={t("chat.delete", "Delete")}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

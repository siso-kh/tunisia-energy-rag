import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import StatusBanner from "./StatusBanner";
import Button from "../ui/Button";
import { useChatStore } from "../../store/chatStore";
import { useChatStream } from "../../hooks/useChatStream";

export default function ChatPanel() {
  const { t } = useTranslation();
  const messages = useChatStore((s) => s.messages);
  const phase = useChatStore((s) => s.phase);
  const reset = useChatStore((s) => s.reset);
  const { sendMessage, cancel } = useChatStream();

  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, phase]);

  const busy = phase !== "idle" && phase !== "error";

  const QUICK_PROMPTS = [
    t("chat.quickTarifs"),
    t("chat.quickSubventions"),
    t("chat.quickCoupures"),
    t("chat.quickNetmetering"),
  ];

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-border bg-card">
      {/* Chat header indicator bar */}
      <header className="flex shrink-0 items-center gap-2.5 border-b border-border px-4 py-2.5">
        <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Sparkles className="size-4" aria-hidden="true" />
        </span>
        <div className="flex min-w-0 items-center gap-2 text-xs">
          <span className="font-semibold text-foreground">{t("chat.title")}</span>
          <span className="text-border">·</span>
          <span className="hidden text-muted-foreground sm:inline">{t("chat.docs")}</span>
          <span className="hidden text-border sm:inline">·</span>
          <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] font-medium text-muted-foreground">
            {t("chat.model")}
          </span>
        </div>
        {messages.length > 0 && (
          <Button variant="ghost" size="sm" className="ms-auto" onClick={reset}>
            ↺ {t("chat.newConversation")}
          </Button>
        )}
      </header>

      {/* Scrollable message container */}
      <div
        ref={scrollRef}
        className="chat-scroll flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto bg-background px-4 py-5"
        aria-live="polite"
      >
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <span className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Sparkles className="size-5" aria-hidden="true" />
            </span>
            <p className="max-w-xs text-sm text-muted-foreground">{t("chat.empty")}</p>
          </div>
        )}
        {messages.map((message, i) => (
          <MessageBubble
            key={i}
            message={message}
            streaming={i === messages.length - 1 && message.role === "assistant" && busy}
          />
        ))}
        <StatusBanner phase={phase} />
      </div>

      {/* Quick prompts + input dock */}
      <div className="shrink-0 border-t border-border bg-card px-4 pb-4 pt-3">
        <div className="mb-2.5 flex flex-wrap gap-1.5">
          {QUICK_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => sendMessage(prompt)}
              disabled={busy}
              className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-primary disabled:opacity-50"
            >
              {prompt}
            </button>
          ))}
        </div>
        <ChatInput onSend={sendMessage} onCancel={cancel} busy={busy} />
      </div>
    </section>
  );
}

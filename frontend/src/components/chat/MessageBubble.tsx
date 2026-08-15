import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ChatMessage } from "../../types";
import SourcesDropdown from "./SourcesDropdown";

interface Props {
  message: ChatMessage;
  streaming?: boolean;
}

export default function MessageBubble({ message, streaming }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} animate-rise`}>
      {isUser ? (
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-relaxed text-primary-foreground">
          <p className="text-pretty whitespace-pre-wrap">{message.content}</p>
        </div>
      ) : (
        <div className="max-w-[85%] rounded-xl border border-border bg-card p-4 text-sm leading-relaxed text-foreground shadow-sm">
          <div className="text-pretty">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>

          {streaming && (
            <span
              data-testid="streaming-cursor"
              className="inline-block h-4 w-2 animate-pulse bg-current ms-1"
            />
          )}

          {message.sources && message.sources.length > 0 && (
            <SourcesDropdown sources={message.sources} />
          )}
        </div>
      )}
    </div>
  );
}

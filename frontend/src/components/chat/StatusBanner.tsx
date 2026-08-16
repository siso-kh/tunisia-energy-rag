import { useTranslation } from "react-i18next";
import Spinner from "../ui/Spinner";
import { useChatStore, type StreamPhase } from "../../store/chatStore";

interface Props {
  phase: StreamPhase;
}

const PHASE_KEYS: Record<string, string> = {
  contextualizing: "chat.thinking",
  searching: "chat.searching",
  generating: "chat.generating",
  error: "chat.error",
};

export default function StatusBanner({ phase }: Props) {
  const { t } = useTranslation();
  const rateLimited = useChatStore((s) => s.errorRateLimited);
  if (phase === "idle") return null;

  const isError = phase === "error";
  const messageKey =
    isError && rateLimited ? "chat.rateLimited" : PHASE_KEYS[phase] ?? "chat.searching";

  return (
    <div
      className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ${
        isError
          ? "bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground"
      }`}
      role={isError ? "alert" : "status"}
    >
      {!isError && <Spinner className="size-3.5" />}
      {t(messageKey)}
    </div>
  );
}

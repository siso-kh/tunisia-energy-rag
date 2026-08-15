import { useTranslation } from "react-i18next";
import Spinner from "../ui/Spinner";
import type { StreamPhase } from "../../store/chatStore";

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
  if (phase === "idle") return null;

  const isError = phase === "error";

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
      {t(PHASE_KEYS[phase] ?? "chat.searching")}
    </div>
  );
}

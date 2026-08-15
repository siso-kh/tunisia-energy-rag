import { FormEvent, useState } from "react";
import { ArrowUp, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import Button from "../ui/Button";

interface Props {
  onSend: (query: string) => void;
  onCancel: () => void;
  busy: boolean;
}

export default function ChatInput({ onSend, onCancel, busy }: Props) {
  const [value, setValue] = useState("");
  const { t } = useTranslation();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const query = value.trim();
    if (!query || busy) return;
    onSend(query);
    setValue("");
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-center gap-2 rounded-xl border border-border bg-background p-1.5 ps-4 transition-colors focus-within:border-primary/50 focus-within:ring-3 focus-within:ring-primary/15"
    >
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t("chat.placeholder")}
        disabled={busy}
        className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-60"
        aria-label={t("chat.placeholder")}
      />
      {busy ? (
        <Button type="button" variant="secondary" size="icon" onClick={onCancel} aria-label="⏹">
          <Square className="size-3.5 fill-current" />
        </Button>
      ) : (
        <Button
          type="submit"
          size="icon"
          className="rounded-lg bg-primary"
          disabled={!value.trim()}
          aria-label={t("chat.send")}
        >
          <ArrowUp className="size-4" aria-hidden="true" />
        </Button>
      )}
    </form>
  );
}

import type { ElementType, ReactNode } from "react";

interface Props {
  title: string;
  tag: string;
  color: string;
  icon: ElementType;
  children: ReactNode;
}

export default function CardShell({ title, tag, color, icon: Icon, children }: Props) {
  return (
    <div className="flex flex-col gap-3.5 rounded-xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="flex size-8 items-center justify-center rounded-lg"
            style={{ backgroundColor: `color-mix(in oklch, ${color} 14%, transparent)`, color }}
          >
            <Icon className="size-4" aria-hidden="true" />
          </span>
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        </div>
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          style={{ backgroundColor: `color-mix(in oklch, ${color} 12%, transparent)`, color }}
        >
          {tag}
        </span>
      </div>
      {children}
    </div>
  );
}

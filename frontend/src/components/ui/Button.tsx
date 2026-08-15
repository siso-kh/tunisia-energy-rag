import { ReactNode, ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "outline";
  size?: "sm" | "md" | "icon";
}

export default function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...rest
}: ButtonProps) {
  const variants: Record<string, string> = {
    primary:
      "bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm",
    secondary:
      "bg-secondary text-secondary-foreground hover:bg-secondary/80",
    outline:
      "border border-border bg-background text-foreground hover:bg-muted",
    ghost: "bg-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
  };
  const sizes: Record<string, string> = {
    sm: "h-7 px-2.5 text-xs gap-1 rounded-lg",
    md: "h-8 px-3 text-sm gap-1.5 rounded-lg",
    icon: "size-8 rounded-lg",
  };

  return (
    <button
      className={`inline-flex shrink-0 items-center justify-center font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

import { forwardRef } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "./cn";

const VARIANTS = {
  primary:
    "bg-ink text-text-inverse hover:bg-ink-800 active:bg-ink-900 shadow-xs disabled:bg-ink-300 disabled:shadow-none",
  secondary:
    "bg-surface text-text-primary border border-border hover:bg-surface-tertiary hover:border-border-strong disabled:text-text-quaternary",
  ghost:
    "bg-transparent text-text-primary hover:bg-surface-tertiary disabled:text-text-quaternary",
  danger:
    "bg-swiss-red text-white hover:bg-swiss-red-hover active:bg-swiss-red-active shadow-xs disabled:bg-swiss-red-100 disabled:text-white",
  brand:
    "bg-swiss-red text-white hover:bg-swiss-red-hover active:bg-swiss-red-active shadow-xs",
  outline:
    "bg-transparent text-text-primary border border-border hover:border-ink hover:bg-surface disabled:text-text-quaternary disabled:border-border",
  link:
    "bg-transparent text-text-primary underline-offset-4 hover:underline px-0",
};

const SIZES = {
  xs: "h-7 px-2.5 text-xs gap-1.5",
  sm: "h-8 px-3 text-sm gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-11 px-5 text-base gap-2",
  xl: "h-12 px-6 text-base gap-2.5",
  icon: "h-9 w-9 p-0",
  "icon-sm": "h-8 w-8 p-0",
  "icon-lg": "h-11 w-11 p-0",
};

const Button = forwardRef(function Button(
  {
    variant = "primary",
    size = "md",
    leftIcon,
    rightIcon,
    loading = false,
    fullWidth = false,
    className,
    children,
    disabled,
    type,
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type || "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium tracking-tight",
        "transition-all duration-150 ease-out select-none",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        "disabled:cursor-not-allowed",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...props}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        leftIcon
      )}
      {children}
      {!loading && rightIcon}
    </button>
  );
});

export default Button;

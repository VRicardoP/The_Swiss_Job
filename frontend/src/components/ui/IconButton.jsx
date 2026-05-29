import { forwardRef } from "react";
import { cn } from "./cn";

const VARIANTS = {
  ghost:   "bg-transparent text-text-secondary hover:bg-surface-tertiary hover:text-text-primary",
  solid:   "bg-ink text-text-inverse hover:bg-ink-800",
  outline: "bg-surface text-text-primary border border-border hover:border-border-strong",
  danger:  "bg-transparent text-text-secondary hover:bg-error-light hover:text-error",
  brand:   "bg-swiss-red text-white hover:bg-swiss-red-hover",
};

const SIZES = {
  sm: "h-8 w-8",
  md: "h-9 w-9",
  lg: "h-10 w-10",
};

const IconButton = forwardRef(function IconButton(
  {
    variant = "ghost",
    size = "md",
    "aria-label": ariaLabel,
    className,
    children,
    type,
    ...props
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type || "button"}
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center justify-center rounded-lg",
        "transition-all duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        "disabled:cursor-not-allowed disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
});

export default IconButton;

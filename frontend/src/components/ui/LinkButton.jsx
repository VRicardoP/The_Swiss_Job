import { forwardRef } from "react";
import { Link } from "react-router-dom";
import { cn } from "./cn";

// Mismas variantes y tamaños que Button.jsx — duplicados intencionalmente
// para evitar acoplamiento entre componentes y permitir divergencia futura.
const VARIANTS = {
  primary:
    "bg-ink text-text-inverse hover:bg-ink-800 active:bg-ink-900 shadow-xs",
  secondary:
    "bg-surface text-text-primary border border-border hover:bg-surface-tertiary hover:border-border-strong",
  ghost:
    "bg-transparent text-text-primary hover:bg-surface-tertiary",
  brand:
    "bg-swiss-red text-white hover:bg-swiss-red-hover active:bg-swiss-red-active shadow-xs",
  outline:
    "bg-transparent text-text-primary border border-border hover:border-ink hover:bg-surface",
  link:
    "bg-transparent text-text-primary underline-offset-4 hover:underline px-0",
};

const SIZES = {
  xs: "h-7 px-2.5 text-xs gap-1.5",
  sm: "h-8 px-3 text-sm gap-1.5",
  md: "h-10 px-4 text-sm gap-2",
  lg: "h-11 px-5 text-base gap-2",
  xl: "h-12 px-6 text-base gap-2.5",
};

const LinkButton = forwardRef(function LinkButton(
  {
    to,
    variant = "primary",
    size = "md",
    leftIcon,
    rightIcon,
    fullWidth = false,
    className,
    children,
    ...props
  },
  ref,
) {
  return (
    <Link
      ref={ref}
      to={to}
      className={cn(
        "inline-flex items-center justify-center rounded-lg font-medium tracking-tight",
        "transition-all duration-150 ease-out select-none",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        VARIANTS[variant],
        SIZES[size],
        fullWidth && "w-full",
        className,
      )}
      {...props}
    >
      {leftIcon}
      {children}
      {rightIcon}
    </Link>
  );
});

export default LinkButton;

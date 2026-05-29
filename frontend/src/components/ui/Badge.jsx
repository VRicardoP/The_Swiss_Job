import { cn } from "./cn";

const VARIANTS = {
  neutral:    "bg-surface-tertiary text-text-secondary border-border",
  solid:      "bg-ink text-text-inverse border-transparent",
  outline:    "bg-transparent text-text-primary border-border",
  brand:      "bg-swiss-red-50 text-swiss-red border-swiss-red-100",
  brandSolid: "bg-swiss-red text-white border-transparent",
  success:    "bg-success-light text-success border-success-border",
  warning:    "bg-warning-light text-warning border-warning-border",
  error:      "bg-error-light text-error border-error-border",
  info:       "bg-info-light text-info border-info-border",
  ink:        "bg-ink-100 text-ink-700 border-ink-200",
};

const SIZES = {
  xs: "h-5 px-1.5 text-[10px] gap-1",
  sm: "h-6 px-2 text-xs gap-1.5",
  md: "h-7 px-2.5 text-xs gap-1.5",
  lg: "h-8 px-3 text-sm gap-2",
};

export default function Badge({
  variant = "neutral",
  size = "sm",
  leftIcon,
  rightIcon,
  className,
  children,
  ...props
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border font-medium tracking-tight whitespace-nowrap",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {leftIcon}
      {children}
      {rightIcon}
    </span>
  );
}

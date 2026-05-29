import { forwardRef } from "react";
import { cn } from "./cn";

const VARIANTS = {
  default:  "bg-surface border border-border",
  elevated: "bg-surface border border-border shadow-card",
  flat:     "bg-surface-tertiary border border-transparent",
  inverse:  "bg-ink text-text-inverse border border-ink-700",
  outline:  "bg-transparent border border-border",
};

const PADDING = {
  none: "",
  sm:   "p-3",
  md:   "p-4",
  lg:   "p-5",
  xl:   "p-6",
};

const Card = forwardRef(function Card(
  {
    variant = "default",
    padding = "md",
    interactive = false,
    className,
    children,
    ...props
  },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-xl",
        VARIANTS[variant],
        PADDING[padding],
        interactive &&
          "transition-all duration-150 hover:border-border-strong hover:shadow-card-hover cursor-pointer",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
});

export function CardHeader({ className, children, ...props }) {
  return (
    <div className={cn("flex items-start justify-between gap-3", className)} {...props}>
      {children}
    </div>
  );
}

export function CardTitle({ className, children, ...props }) {
  return (
    <h3
      className={cn("text-base font-semibold text-text-primary tracking-tight", className)}
      {...props}
    >
      {children}
    </h3>
  );
}

export function CardDescription({ className, children, ...props }) {
  return (
    <p className={cn("text-sm text-text-secondary", className)} {...props}>
      {children}
    </p>
  );
}

export function CardContent({ className, children, ...props }) {
  return (
    <div className={cn("mt-3", className)} {...props}>
      {children}
    </div>
  );
}

export function CardFooter({ className, children, ...props }) {
  return (
    <div
      className={cn("mt-4 flex items-center justify-between gap-3", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export default Card;

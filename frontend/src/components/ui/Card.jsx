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

// DT-148 B7 (2026-10-21): aquí vivían CardHeader, CardTitle, CardDescription,
// CardContent y CardFooter. Ninguna tenía importador: sólo las re-exportaba
// `ui/index.js`. Las tarjetas de este proyecto componen su interior con
// utilidades del tema directamente.

export default Card;

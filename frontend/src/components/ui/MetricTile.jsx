import { cn } from "./cn";

const TONES = {
  neutral: "text-text-primary",
  brand:   "text-swiss-red",
  ink:     "text-ink",
  success: "text-success",
  warning: "text-warning",
  error:   "text-error",
  info:    "text-info",
};

export default function MetricTile({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
  trend, // { value: "+12%", direction: "up" | "down" | "flat" }
  className,
  ...props
}) {
  return (
    <div
      className={cn(
        "rounded-xl bg-surface border border-border p-4",
        "flex flex-col gap-1",
        className,
      )}
      {...props}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-text-tertiary">
          {label}
        </span>
        {Icon && <Icon className="h-4 w-4 text-text-quaternary" aria-hidden="true" />}
      </div>

      <div className="flex items-baseline gap-2">
        <span
          className={cn(
            "text-2xl font-semibold tracking-tight tabular-nums",
            TONES[tone],
          )}
        >
          {value}
        </span>
        {trend && (
          <span
            className={cn(
              "text-xs font-medium",
              trend.direction === "up" && "text-success",
              trend.direction === "down" && "text-error",
              trend.direction === "flat" && "text-text-tertiary",
            )}
          >
            {trend.value}
          </span>
        )}
      </div>

      {hint && <p className="text-xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

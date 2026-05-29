import { cn } from "./cn";

export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  className,
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        "rounded-2xl border border-dashed border-border bg-surface",
        "px-6 py-12",
        className,
      )}
    >
      {Icon && (
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-surface-tertiary text-text-secondary">
          <Icon className="h-6 w-6" aria-hidden="true" />
        </div>
      )}
      {title && (
        <h3 className="text-base font-semibold text-text-primary tracking-tight">
          {title}
        </h3>
      )}
      {description && (
        <p className="mt-1.5 max-w-md text-sm text-text-secondary">
          {description}
        </p>
      )}
      {(action || secondaryAction) && (
        <div className="mt-5 flex items-center gap-2">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

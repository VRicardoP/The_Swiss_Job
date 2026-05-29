import { cn } from "./ui";

// Tabs/chips de categoría reutilizables (MatchPage, SavedJobsPage).
// Recibe una lista de items {id, label, shortLabel?, count} y la categoría activa.
export default function CategoryTabs({
  categories,
  activeId,
  onChange,
  className,
  ariaLabel = "Categories",
}) {
  if (!categories || categories.length === 0) return null;

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn("flex flex-wrap gap-1.5", className)}
    >
      {categories.map((cat) => {
        const isActive = activeId === cat.id;
        return (
          <button
            key={cat.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(isActive ? null : cat.id)}
            className={cn(
              "group inline-flex items-center gap-1.5 rounded-full border h-8 px-3 text-xs font-medium",
              "transition-all duration-150",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface-secondary",
              isActive
                ? "bg-ink text-text-inverse border-ink"
                : "bg-surface text-text-secondary border-border hover:border-border-strong hover:text-text-primary",
            )}
          >
            {cat.id && cat.id !== cat.label && (
              <span className="font-semibold tabular-nums">{cat.id}</span>
            )}
            <span className={cn(cat.shortLabel && "hidden sm:inline")}>
              {cat.label}
            </span>
            {cat.shortLabel && (
              <span className="sm:hidden">{cat.shortLabel}</span>
            )}
            <span
              className={cn(
                "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-semibold tabular-nums",
                isActive
                  ? "bg-text-inverse/15 text-text-inverse"
                  : "bg-surface-tertiary text-text-tertiary",
              )}
            >
              {cat.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}

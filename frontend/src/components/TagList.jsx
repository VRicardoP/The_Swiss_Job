import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Button, cn } from "./ui";

// Lista editable de tags (skills, locations, etc.).
// Controlada: padre pasa `items` (array de strings) y `onChange`.
export default function TagList({
  items,
  onChange,
  placeholder = "Add item",
  emptyText = "No items yet",
  variant = "neutral", // neutral | brand
  inputAriaLabel = "Add new item",
  className,
}) {
  const [value, setValue] = useState("");

  const variants = {
    neutral: "bg-surface-tertiary text-text-primary border-border",
    brand:   "bg-swiss-red-50 text-swiss-red border-swiss-red-100",
  };

  function add(e) {
    e.preventDefault();
    const v = value.trim();
    if (v && !items.includes(v)) {
      onChange([...items, v]);
    }
    setValue("");
  }

  function remove(item) {
    onChange(items.filter((i) => i !== item));
  }

  return (
    <div className={cn("space-y-2.5", className)}>
      {items.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {items.map((item) => (
            <span
              key={item}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border h-7 pl-2.5 pr-1 text-xs font-medium",
                variants[variant],
              )}
            >
              {item}
              <button
                type="button"
                onClick={() => remove(item)}
                aria-label={`Remove ${item}`}
                className={cn(
                  "inline-flex h-5 w-5 items-center justify-center rounded-full transition-colors",
                  "hover:bg-ink/10",
                )}
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <p className="text-xs text-text-tertiary italic">{emptyText}</p>
      )}

      <form onSubmit={add} className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          aria-label={inputAriaLabel}
          className={cn(
            "h-9 flex-1 rounded-lg border border-border bg-surface px-3 text-sm",
            "placeholder:text-text-quaternary text-text-primary",
            "transition-all duration-150",
            "focus:outline-none focus:border-ink focus:shadow-ring",
          )}
        />
        <Button
          type="submit"
          variant="secondary"
          size="sm"
          leftIcon={<Plus className="h-3.5 w-3.5" />}
          disabled={!value.trim()}
        >
          Add
        </Button>
      </form>
    </div>
  );
}

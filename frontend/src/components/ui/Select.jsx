import { forwardRef, useId } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

const SIZES = {
  sm: "h-8 text-sm pl-2.5 pr-8",
  md: "h-10 text-sm pl-3 pr-9",
  lg: "h-11 text-base pl-3.5 pr-10",
};

const Select = forwardRef(function Select(
  {
    label,
    hint,
    error,
    size = "md",
    fullWidth = true,
    className,
    containerClassName,
    id,
    required,
    children,
    ...props
  },
  ref,
) {
  const autoId = useId();
  const selectId = id ?? autoId;

  return (
    <div className={cn("flex flex-col gap-1.5", fullWidth && "w-full", containerClassName)}>
      {label && (
        <label htmlFor={selectId} className="text-sm font-medium text-text-primary">
          {label}
          {required && <span className="text-swiss-red ml-0.5">*</span>}
        </label>
      )}

      <div className="relative">
        <select
          ref={ref}
          id={selectId}
          required={required}
          aria-invalid={!!error || undefined}
          className={cn(
            "w-full appearance-none bg-surface border border-border rounded-lg",
            "text-text-primary outline-none",
            "transition-all duration-150",
            "focus:border-ink focus:shadow-ring",
            "disabled:text-text-tertiary disabled:cursor-not-allowed",
            error && "border-error focus:border-error focus:shadow-[0_0_0_3px_rgba(185,28,28,0.15)]",
            SIZES[size],
            className,
          )}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-tertiary"
          aria-hidden="true"
        />
      </div>

      {hint && !error && <p className="text-xs text-text-tertiary">{hint}</p>}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  );
});

export default Select;

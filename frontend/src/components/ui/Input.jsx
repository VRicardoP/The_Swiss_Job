import { forwardRef, useId } from "react";
import { cn } from "./cn";

const SIZES = {
  sm: "h-8 text-sm px-2.5",
  md: "h-10 text-sm px-3",
  lg: "h-11 text-base px-3.5",
};

const Input = forwardRef(function Input(
  {
    label,
    hint,
    error,
    leftIcon,
    rightIcon,
    size = "md",
    fullWidth = true,
    className,
    containerClassName,
    id,
    required,
    ...props
  },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const describedBy = [];
  if (hint) describedBy.push(`${inputId}-hint`);
  if (error) describedBy.push(`${inputId}-error`);

  return (
    <div className={cn("flex flex-col gap-1.5", fullWidth && "w-full", containerClassName)}>
      {label && (
        <label
          htmlFor={inputId}
          className="text-sm font-medium text-text-primary"
        >
          {label}
          {required && <span className="text-swiss-red ml-0.5">*</span>}
        </label>
      )}

      <div
        className={cn(
          "relative flex items-center bg-surface border border-border rounded-lg",
          "transition-all duration-150",
          "focus-within:border-ink focus-within:shadow-ring",
          error && "border-error focus-within:border-error focus-within:shadow-[0_0_0_3px_rgba(185,28,28,0.15)]",
        )}
      >
        {leftIcon && (
          <span className="pl-3 text-text-tertiary flex items-center pointer-events-none">
            {leftIcon}
          </span>
        )}
        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={!!error || undefined}
          aria-describedby={describedBy.join(" ") || undefined}
          className={cn(
            "w-full bg-transparent outline-none placeholder:text-text-quaternary text-text-primary",
            "disabled:text-text-tertiary disabled:cursor-not-allowed",
            SIZES[size],
            leftIcon && "pl-2",
            rightIcon && "pr-2",
            className,
          )}
          {...props}
        />
        {rightIcon && (
          <span className="pr-3 text-text-tertiary flex items-center">
            {rightIcon}
          </span>
        )}
      </div>

      {hint && !error && (
        <p id={`${inputId}-hint`} className="text-xs text-text-tertiary">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${inputId}-error`} className="text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
});

export default Input;

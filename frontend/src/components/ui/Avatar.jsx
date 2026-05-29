import { useState } from "react";
import { cn } from "./cn";

const SIZES = {
  xs: "h-6 w-6 text-[10px]",
  sm: "h-8 w-8 text-xs",
  md: "h-10 w-10 text-sm",
  lg: "h-12 w-12 text-base",
  xl: "h-16 w-16 text-lg",
};

const PALETTE = [
  "bg-ink-100 text-ink-700",
  "bg-info-light text-info",
  "bg-success-light text-success",
  "bg-warning-light text-warning",
  "bg-swiss-red-50 text-swiss-red",
  "bg-surface-tertiary text-ink-700",
];

function hashIndex(str, mod) {
  let h = 0;
  for (let i = 0; i < (str || "").length; i++) {
    h = (h * 31 + str.charCodeAt(i)) >>> 0;
  }
  return h % mod;
}

function initialsOf(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).filter(Boolean).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() ?? "").join("") || name[0]?.toUpperCase() || "?";
}

export default function Avatar({
  name,
  src,
  size = "md",
  shape = "rounded",
  className,
  ...props
}) {
  const [imgError, setImgError] = useState(false);
  const showImage = src && !imgError;
  const colorClass = PALETTE[hashIndex(name || "", PALETTE.length)];

  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden font-semibold tracking-tight select-none",
        shape === "circle" ? "rounded-full" : "rounded-lg",
        SIZES[size],
        !showImage && colorClass,
        className,
      )}
      aria-label={name || undefined}
      {...props}
    >
      {showImage ? (
        <img
          src={src}
          alt={name || ""}
          className="h-full w-full object-cover"
          onError={() => setImgError(true)}
        />
      ) : (
        <span>{initialsOf(name)}</span>
      )}
    </div>
  );
}

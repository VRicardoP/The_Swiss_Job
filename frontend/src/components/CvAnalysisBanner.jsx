import { Sparkles, CheckCircle2, AlertCircle, X } from "lucide-react";

// Clases literales completas por tono (Tailwind v4 las escanea de aquí).
const TONE = {
  info: {
    box: "border-info-border bg-info-light",
    text: "text-info",
    bar: "bg-info",
  },
  success: {
    box: "border-success-border bg-success-light",
    text: "text-success",
    bar: "bg-success",
  },
  error: {
    box: "border-error-border bg-error-light",
    text: "text-error",
    bar: "bg-error",
  },
};

/**
 * Aviso + barra de progreso del autocompletado del perfil desde el CV.
 * Alimentado por el hook useCvAnalysis (eventos SSE del backend).
 */
export default function CvAnalysisBanner({
  active,
  stage,
  percent,
  message,
  onDismiss,
}) {
  if (!active) return null;

  const tone = stage === "error" ? "error" : stage === "done" ? "success" : "info";
  const t = TONE[tone];
  const Icon =
    stage === "error" ? AlertCircle : stage === "done" ? CheckCircle2 : Sparkles;
  const inProgress = stage !== "done" && stage !== "error";

  return (
    <div className={`rounded-lg border p-3 animate-fade-in ${t.box}`}>
      <div className="flex items-center justify-between gap-2">
        <p className={`flex items-center gap-1.5 text-sm font-medium ${t.text}`}>
          <Icon className={`h-4 w-4 shrink-0 ${inProgress ? "animate-pulse" : ""}`} />
          {message || "Analyzing your CV to auto-complete your profile…"}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          {stage !== "error" && (
            <span className="text-xs tabular-nums text-text-tertiary">
              {percent}%
            </span>
          )}
          {!inProgress && (
            <button
              type="button"
              onClick={onDismiss}
              aria-label="Dismiss"
              className="text-text-tertiary transition-colors hover:text-text-primary"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {stage !== "error" && (
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-tertiary">
          <div
            className={`h-full rounded-full transition-all duration-500 ${t.bar}`}
            style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
          />
        </div>
      )}

      {inProgress && (
        <p className="mt-1.5 text-xs text-text-tertiary">
          You can keep editing — we'll fill in the fields automatically.
        </p>
      )}
    </div>
  );
}

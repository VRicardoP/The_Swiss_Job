import { memo } from "react";
import { Link } from "react-router-dom";
import {
  ThumbsUp,
  ThumbsDown,
  ArrowUpRight,
  Languages,
  Check,
  MapPin,
  Building2,
  Sparkles,
  GraduationCap,
  Flame,
} from "lucide-react";
import { Avatar, Badge, cn } from "./ui";

function formatSalary(min, max) {
  if (!min && !max) return null;
  const fmt = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);
  if (min && max) return `${fmt(min)}–${fmt(max)} CHF`;
  if (min) return `from ${fmt(min)} CHF`;
  return `up to ${fmt(max)} CHF`;
}

function toneForScore(value) {
  if (value >= 70) return { ring: "stroke-success", text: "text-success", track: "stroke-success/15" };
  if (value >= 40) return { ring: "stroke-warning", text: "text-warning", track: "stroke-warning/15" };
  return { ring: "stroke-error", text: "text-error", track: "stroke-error/15" };
}

function ScoreRing({ value, size = 56 }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - v / 100);
  const tone = toneForScore(v);
  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      aria-label={`Match score ${v} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          className={cn("stroke-ink/10", tone.track)}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          className={cn("transition-all duration-500 ease-out", tone.ring)}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className={cn("text-base font-semibold tracking-tight tabular-nums", tone.text)}>
          {v}
        </span>
      </div>
    </div>
  );
}

function ScoreBar({ label, value }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className="flex items-center gap-2.5">
      <span className="w-16 text-[11px] uppercase tracking-wider text-text-tertiary">
        {label}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-tertiary">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-500",
            v >= 70 ? "bg-success" : v >= 40 ? "bg-ink-500" : "bg-ink-300",
          )}
          style={{ width: `${v}%` }}
        />
      </div>
      <span className="w-7 text-right text-[11px] font-medium tabular-nums text-text-secondary">
        {v}
      </span>
    </div>
  );
}

function FeedbackButton({ active, activeTone = "success", icon, label, onClick, title }) {
  const tones = {
    success: "bg-success-light text-success ring-1 ring-success-border",
    error:   "bg-error-light text-error ring-1 ring-error-border",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={active}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium transition-all duration-150",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-1",
        active
          ? tones[activeTone]
          : "bg-surface text-text-secondary border border-border hover:border-border-strong hover:text-text-primary",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function MatchCard({ match, onFeedback, onClearFeedback, onImplicit }) {
  const salary = formatSalary(match.job_salary_min, match.job_salary_max);

  function handleJobClick() {
    onImplicit?.({ jobHash: match.job_hash, action: "opened" });
  }

  function toggleFeedback(kind) {
    if (match.feedback === kind) {
      onClearFeedback?.({ jobHash: match.job_hash });
    } else {
      onFeedback?.({ jobHash: match.job_hash, feedback: kind });
    }
  }

  const title = match.job_title_en || match.job_title;

  return (
    <article className="group rounded-xl border border-border bg-surface p-4 sm:p-5 transition-all duration-150 hover:border-border-strong hover:shadow-card-hover">
      <div className="flex gap-3 sm:gap-4">
        <Avatar name={match.job_company || "?"} size="md" />

        <div className="min-w-0 flex-1">
          {/* Encabezado */}
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <Link
                to={`/job/${match.job_hash}`}
                onClick={handleJobClick}
                className="block truncate text-[15px] font-semibold tracking-tight text-text-primary hover:text-ink"
                title={match.job_title_en ? match.job_title : undefined}
              >
                {title}
              </Link>
              {match.job_title_en && (
                <span className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-text-quaternary">
                  <Languages className="h-3 w-3" aria-hidden="true" />
                  Translated from {match.job_language?.toUpperCase()}
                </span>
              )}
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-text-secondary">
                {match.job_company && (
                  <span className="inline-flex items-center gap-1 truncate">
                    <Building2 className="h-3.5 w-3.5 text-text-quaternary" aria-hidden="true" />
                    <span className="truncate">{match.job_company}</span>
                  </span>
                )}
                {match.job_location && (
                  <span className="inline-flex items-center gap-1">
                    <MapPin className="h-3.5 w-3.5 text-text-quaternary" aria-hidden="true" />
                    {match.job_location}
                  </span>
                )}
              </p>
            </div>

            <ScoreRing value={match.score_final} />
          </div>

          {/* Meta: source + watchlist + urgency + salary */}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {match.school_id && (
              <Badge
                variant="success"
                size="xs"
                leftIcon={<GraduationCap className="h-3 w-3" />}
                title={`Watchlist • ${match.school_policy ?? ""}`}
              >
                Watchlist
              </Badge>
            )}
            {match.urgency_score >= 30 && (
              <Badge
                variant="warning"
                size="xs"
                leftIcon={<Flame className="h-3 w-3" />}
                title="Urgency score"
              >
                urg {Math.round(match.urgency_score)}
              </Badge>
            )}
            {match.application_status && match.application_status !== "detected" && (
              <Badge variant="info" size="xs" title="Estado candidatura">
                {match.application_status}
              </Badge>
            )}
            {match.job_source && (
              <Badge variant="neutral" size="xs">
                {match.job_source}
              </Badge>
            )}
            {salary && (
              <span className="ml-auto text-sm font-semibold tabular-nums text-text-primary">
                {salary}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Breakdown */}
      <div className="mt-4 grid gap-2 rounded-lg bg-surface-secondary p-3 sm:grid-cols-2">
        <ScoreBar label="Skills" value={match.scores.embedding * 100} />
        <ScoreBar label="Salary" value={match.scores.salary * 100} />
        <ScoreBar label="Location" value={match.scores.location * 100} />
        <ScoreBar label="Recency" value={match.scores.recency * 100} />
        {match.scores.llm > 0 && (
          <div className="sm:col-span-2">
            <ScoreBar label="AI" value={match.scores.llm * 100} />
          </div>
        )}
      </div>

      {/* Skills matching / missing */}
      {(match.matching_skills.length > 0 || match.missing_skills.length > 0) && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {match.matching_skills.map((skill) => (
            <Badge key={skill} variant="success" size="xs" leftIcon={<Check className="h-3 w-3" />}>
              {skill}
            </Badge>
          ))}
          {match.missing_skills.map((skill) => (
            <Badge key={skill} variant="neutral" size="xs" className="line-through opacity-60">
              {skill}
            </Badge>
          ))}
        </div>
      )}

      {/* Explicación LLM */}
      {match.explanation && (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-info-border bg-info-light/60 p-3">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-info" aria-hidden="true" />
          <p className="text-xs leading-relaxed text-info">{match.explanation}</p>
        </div>
      )}

      {/* Acciones */}
      <div className="mt-4 flex items-center gap-2 border-t border-border-light pt-3">
        <FeedbackButton
          active={match.feedback === "thumbs_up"}
          activeTone="success"
          icon={<ThumbsUp className="h-3.5 w-3.5" />}
          label="Good"
          onClick={() => toggleFeedback("thumbs_up")}
          title={match.feedback === "thumbs_up" ? "Unmark" : "Save match"}
        />
        <FeedbackButton
          active={match.feedback === "thumbs_down"}
          activeTone="error"
          icon={<ThumbsDown className="h-3.5 w-3.5" />}
          label="Not for me"
          onClick={() => toggleFeedback("thumbs_down")}
          title={match.feedback === "thumbs_down" ? "Unmark" : "Dismiss"}
        />
        <Link
          to={`/job/${match.job_hash}`}
          onClick={handleJobClick}
          className="ml-auto inline-flex items-center gap-1 text-sm font-medium text-text-primary hover:text-ink"
        >
          View details
          <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </div>
    </article>
  );
}

export default memo(MatchCard);

import { memo } from "react";
import { Link } from "react-router-dom";
import { MapPin, Globe2, Building2 } from "lucide-react";
import { Avatar, Badge, cn } from "./ui";

function formatSalary(min, max) {
  if (!min && !max) return null;
  const fmt = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);
  if (min && max) return `${fmt(min)}–${fmt(max)} CHF`;
  if (min) return `from ${fmt(min)} CHF`;
  return `up to ${fmt(max)} CHF`;
}

function formatPosted(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  const days = Math.floor(diff / 86400);
  if (days < 30) return `${days}d`;
  return `${Math.floor(days / 30)}mo`;
}

function JobCard({ job }) {
  const salary = formatSalary(job.salary_min_chf, job.salary_max_chf);
  const posted = formatPosted(job.published_at || job.created_at);

  return (
    <Link
      to={`/job/${job.hash}`}
      className={cn(
        "group block rounded-xl border border-border bg-surface p-4 sm:p-5",
        "transition-all duration-150",
        "hover:border-border-strong hover:shadow-card-hover",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface-secondary",
      )}
    >
      <div className="flex gap-3 sm:gap-4">
        <Avatar name={job.company || "?"} size="md" />

        <div className="min-w-0 flex-1">
          {/* Encabezado: title + salario */}
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-[15px] font-semibold tracking-tight text-text-primary group-hover:text-ink">
                {job.title}
              </h3>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm text-text-secondary">
                {job.company && (
                  <span className="inline-flex items-center gap-1 truncate">
                    <Building2 className="h-3.5 w-3.5 text-text-quaternary" aria-hidden="true" />
                    <span className="truncate">{job.company}</span>
                  </span>
                )}
                {(job.canton || job.location) && (
                  <span className="inline-flex items-center gap-1">
                    <MapPin className="h-3.5 w-3.5 text-text-quaternary" aria-hidden="true" />
                    {job.canton || job.location}
                  </span>
                )}
                {job.is_remote && (
                  <span className="inline-flex items-center gap-1 text-success">
                    <Globe2 className="h-3.5 w-3.5" aria-hidden="true" />
                    Remote
                  </span>
                )}
              </p>
            </div>

            {salary && (
              <div className="shrink-0 rounded-md bg-ink-50 px-2 py-1 text-right">
                <span className="block text-[10px] font-medium uppercase tracking-wider text-text-tertiary">
                  Salary
                </span>
                <span className="block text-sm font-semibold tabular-nums text-text-primary">
                  {salary}
                </span>
              </div>
            )}
          </div>

          {/* Snippet */}
          {job.description_snippet && (
            <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-text-secondary">
              {job.description_snippet}
            </p>
          )}

          {/* Tags + fuente + tiempo */}
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {job.seniority && (
              <Badge variant="ink" size="xs">
                {job.seniority}
              </Badge>
            )}
            {job.contract_type && (
              <Badge variant="neutral" size="xs">
                {job.contract_type.replace("_", " ")}
              </Badge>
            )}
            {job.language && (
              <Badge variant="outline" size="xs">
                {job.language.toUpperCase()}
              </Badge>
            )}

            <span className="ml-auto inline-flex items-center gap-2 text-[11px] text-text-tertiary">
              {posted && <span className="tabular-nums">{posted}</span>}
              {job.source && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="truncate max-w-32">{job.source}</span>
                </>
              )}
            </span>
          </div>
        </div>
      </div>
    </Link>
  );
}

export default memo(JobCard);

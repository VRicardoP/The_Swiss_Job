import { memo } from "react";
import { Link } from "react-router-dom";

const SOURCE_COLORS = {
  jobicy: "bg-purple-100 text-purple-700",
  remotive: "bg-green-100 text-green-700",
  arbeitnow: "bg-blue-100 text-blue-700",
  remoteok: "bg-orange-100 text-orange-700",
  himalayas: "bg-teal-100 text-teal-700",
  weworkremotely: "bg-rose-100 text-rose-700",
  swisstechtjobs: "bg-red-100 text-red-700",
  ictjobs: "bg-indigo-100 text-indigo-700",
};

function formatSalary(min, max) {
  if (!min && !max) return null;
  const fmt = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);
  if (min && max) return `${fmt(min)}–${fmt(max)} CHF`;
  if (min) return `ab ${fmt(min)} CHF`;
  return `bis ${fmt(max)} CHF`;
}

function JobCard({ job }) {
  const salary = formatSalary(job.salary_min_chf, job.salary_max_chf);
  const colorClass = SOURCE_COLORS[job.source] || "bg-gray-100 text-gray-700";
  const initial = job.company ? job.company[0].toUpperCase() : "?";

  return (
    <Link
      to={`/job/${job.hash}`}
      className="block bg-surface rounded-xl shadow-card hover:shadow-card-hover hover:-translate-y-0.5 p-5 transition-all duration-200"
    >
      <div className="flex gap-3">
        {/* Company initial */}
        <div className="shrink-0 w-11 h-11 rounded-xl bg-swiss-red-light flex items-center justify-center text-lg font-bold text-swiss-red">
          {initial}
        </div>

        <div className="min-w-0 flex-1">
          {/* Title + company */}
          <h3 className="text-[15px] font-semibold text-text-primary truncate">
            {job.title}
          </h3>
          <p className="text-sm text-text-secondary truncate">
            {job.company}
            {job.canton && ` · ${job.canton}`}
            {job.is_remote && " · Remote"}
          </p>

          {/* Snippet */}
          {job.description_snippet && (
            <p className="mt-1 text-sm text-text-secondary leading-relaxed line-clamp-2">
              {job.description_snippet}
            </p>
          )}

          {/* Badges + salary */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span
              className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-surface-tertiary text-text-secondary`}
            >
              {job.source}
            </span>
            {job.language && (
              <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-info-light text-info">
                {job.language.toUpperCase()}
              </span>
            )}
            {job.seniority && (
              <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-warning-light text-warning">
                {job.seniority}
              </span>
            )}
            {job.contract_type && (
              <span className="inline-block px-2 py-0.5 rounded-full text-[10px] font-medium bg-success-light text-success">
                {job.contract_type}
              </span>
            )}
            {salary && (
              <span className="ml-auto text-sm font-semibold text-swiss-red">
                {salary}
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}

export default memo(JobCard);

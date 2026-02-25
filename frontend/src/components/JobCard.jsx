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

export default function JobCard({ job }) {
  const salary = formatSalary(job.salary_min_chf, job.salary_max_chf);
  const colorClass = SOURCE_COLORS[job.source] || "bg-gray-100 text-gray-700";
  const initial = job.company ? job.company[0].toUpperCase() : "?";

  return (
    <Link
      to={`/job/${job.hash}`}
      className="block bg-white rounded-lg border border-gray-200 p-4 hover:shadow-md transition-shadow"
    >
      <div className="flex gap-3">
        {/* Company initial */}
        <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center text-lg font-semibold text-gray-500">
          {initial}
        </div>

        <div className="min-w-0 flex-1">
          {/* Title + company */}
          <h3 className="text-sm font-semibold text-gray-900 truncate">
            {job.title}
          </h3>
          <p className="text-xs text-gray-500 truncate">
            {job.company}
            {job.canton && ` · ${job.canton}`}
            {job.is_remote && " · Remote"}
          </p>

          {/* Snippet */}
          {job.description_snippet && (
            <p className="mt-1 text-xs text-gray-600 line-clamp-2">
              {job.description_snippet}
            </p>
          )}

          {/* Badges + salary */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span
              className={`inline-block px-2 py-0.5 rounded text-[10px] font-medium ${colorClass}`}
            >
              {job.source}
            </span>
            {job.language && (
              <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-sky-100 text-sky-700">
                {job.language.toUpperCase()}
              </span>
            )}
            {job.seniority && (
              <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700">
                {job.seniority}
              </span>
            )}
            {job.contract_type && (
              <span className="inline-block px-2 py-0.5 rounded text-[10px] font-medium bg-emerald-100 text-emerald-700">
                {job.contract_type}
              </span>
            )}
            {salary && (
              <span className="ml-auto text-xs font-medium text-gray-700">
                {salary}
              </span>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}

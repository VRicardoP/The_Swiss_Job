import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { jobsApi } from "../config/api";
import useAuthStore from "../stores/authStore";
import DocumentGenerator from "../components/DocumentGenerator";

function formatSalary(min, max) {
  if (!min && !max) return null;
  const fmt = (v) =>
    v >= 1000
      ? `${new Intl.NumberFormat("de-CH").format(v)} CHF`
      : `${v} CHF`;
  if (min && max) return `${fmt(min)} – ${fmt(max)}`;
  if (min) return `ab ${fmt(min)}`;
  return `bis ${fmt(max)}`;
}

function formatDate(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString("de-CH", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function JobDetailPage() {
  const { hash } = useParams();
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);

  const { data: job, isLoading, isError, error } = useQuery({
    queryKey: ["job", hash],
    queryFn: () => jobsApi.getJob(hash),
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-gray-900" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="min-h-screen bg-gray-50 p-4">
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-gray-600 mb-4 hover:text-gray-900"
        >
          &larr; Back
        </button>
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
          {error.status === 404 ? "Job not found." : `Error: ${error.message}`}
        </div>
      </div>
    );
  }

  const salary = formatSalary(job.salary_min_chf, job.salary_max_chf);
  const initial = job.company ? job.company[0].toUpperCase() : "?";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-2xl mx-auto px-4 py-3">
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-gray-600 hover:text-gray-900 mb-2 flex items-center gap-1"
          >
            <svg
              className="w-4 h-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 19l-7-7 7-7"
              />
            </svg>
            Back
          </button>

          <div className="flex gap-3 items-start">
            <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-gray-100 flex items-center justify-center text-xl font-semibold text-gray-500">
              {initial}
            </div>
            <div className="min-w-0">
              <h1 className="text-lg font-bold text-gray-900">{job.title}</h1>
              <p className="text-sm text-gray-600">
                {job.company}
                {job.location && ` · ${job.location}`}
                {job.canton && ` (${job.canton})`}
              </p>
            </div>
          </div>

          {/* Badges */}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <span className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-700">
              {job.source}
            </span>
            {job.is_remote && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700">
                Remote
              </span>
            )}
            {job.language && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-sky-100 text-sky-700">
                {job.language.toUpperCase()}
              </span>
            )}
            {job.seniority && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700">
                {job.seniority}
              </span>
            )}
            {job.contract_type && (
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-emerald-100 text-emerald-700">
                {job.contract_type}
              </span>
            )}
          </div>

          {salary && (
            <p className="mt-2 text-sm font-semibold text-gray-900">
              {salary}
            </p>
          )}
        </div>
      </header>

      {/* Body */}
      <main className="max-w-2xl mx-auto px-4 py-4">
        {/* Description */}
        {job.description && (
          <div
            className="prose prose-sm max-w-none text-gray-700"
            dangerouslySetInnerHTML={{ __html: job.description }}
          />
        )}

        {/* Tags */}
        {job.tags && job.tags.length > 0 && (
          <div className="mt-6">
            <h3 className="text-xs font-medium text-gray-500 uppercase mb-2">
              Skills
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {job.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-600"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Timestamps */}
        <div className="mt-6 text-xs text-gray-400 space-y-0.5">
          {job.published_at && <p>Published: {formatDate(job.published_at)}</p>}
          {job.first_seen_at && (
            <p>First seen: {formatDate(job.first_seen_at)}</p>
          )}
        </div>

        {/* Apply button */}
        {job.url && (
          <a
            href={job.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-6 block w-full text-center rounded-lg bg-gray-900 px-4 py-3 text-sm font-medium text-white hover:bg-gray-800"
          >
            Apply on {job.source}
          </a>
        )}

        {/* AI Document Generator (authenticated users only) */}
        {token && (
          <DocumentGenerator
            jobHash={hash}
            jobTitle={job.title}
            jobCompany={job.company}
          />
        )}
      </main>
    </div>
  );
}

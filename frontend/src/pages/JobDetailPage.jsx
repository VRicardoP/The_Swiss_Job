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
      <div className="min-h-screen flex items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="min-h-screen p-4">
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-text-secondary hover:text-swiss-red transition-colors mb-4"
        >
          &larr; Back
        </button>
        <div className="bg-error-light text-error rounded-xl p-4 text-sm">
          {error.status === 404 ? "Job not found." : `Error: ${error.message}`}
        </div>
      </div>
    );
  }

  const salary = formatSalary(job.salary_min_chf, job.salary_max_chf);
  const initial = job.company ? job.company[0].toUpperCase() : "?";

  return (
    <div className="min-h-screen">
      {/* Header */}
      <header className="bg-surface shadow-card rounded-b-xl">
        <div className="max-w-3xl mx-auto px-4 py-4">
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-text-secondary hover:text-swiss-red transition-colors mb-3 flex items-center gap-1"
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
            <div className="shrink-0 w-14 h-14 rounded-xl bg-swiss-red-light flex items-center justify-center text-xl font-bold text-swiss-red">
              {initial}
            </div>
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-text-primary tracking-tight">{job.title}</h1>
              <p className="text-sm text-text-secondary">
                {job.company}
                {job.location && ` · ${job.location}`}
                {job.canton && ` (${job.canton})`}
              </p>
            </div>
          </div>

          {/* Badges */}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-surface-tertiary text-text-secondary">
              {job.source}
            </span>
            {job.is_remote && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-info-light text-info">
                Remote
              </span>
            )}
            {job.language && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-info-light text-info">
                {job.language.toUpperCase()}
              </span>
            )}
            {job.seniority && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-warning-light text-warning">
                {job.seniority}
              </span>
            )}
            {job.contract_type && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-success-light text-success">
                {job.contract_type}
              </span>
            )}
          </div>

          {salary && (
            <p className="mt-2 text-lg font-bold text-swiss-red">
              {salary}
            </p>
          )}
        </div>
      </header>

      {/* Body */}
      <main className="max-w-3xl mx-auto px-4 py-6">
        {/* Description */}
        {job.description && (
          <div className="bg-surface p-6 rounded-xl shadow-card">
            <div
              className="prose prose-sm prose-headings:text-text-primary prose-p:text-text-secondary prose-a:text-swiss-red max-w-none"
              dangerouslySetInnerHTML={{ __html: job.description }}
            />
          </div>
        )}

        {/* Tags */}
        {job.tags && job.tags.length > 0 && (
          <div className="mt-6">
            <h3 className="text-xs font-medium text-text-tertiary uppercase mb-2">
              Skills
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {job.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2.5 py-0.5 rounded-full text-xs bg-surface-tertiary text-text-secondary"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Timestamps */}
        <div className="mt-6 text-xs text-text-tertiary space-y-0.5">
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
            className="mt-6 block w-full text-center bg-swiss-red hover:bg-swiss-red-hover rounded-xl px-4 py-3 text-base font-semibold text-white shadow-card hover:shadow-card-hover transition-all duration-200"
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

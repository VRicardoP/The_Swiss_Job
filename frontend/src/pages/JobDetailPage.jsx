import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { jobsApi } from "../config/api";
import useAuthStore from "../stores/authStore";
import DocumentGenerator from "../components/DocumentGenerator";
import { sanitizeHtml } from "../utils/sanitizeHtml";
import { cn } from "../components/ui";

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
  const user = useAuthStore((s) => s.user);

  const { data: job, isLoading, isError, error } = useQuery({
    queryKey: ["job", hash],
    queryFn: () => jobsApi.getJob(hash),
  });

  if (isLoading) {
    return (
      <div className="min-h-screen-safe flex items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="min-h-screen-safe p-4">
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
    <div className="min-h-screen-safe">
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
            {job.remote && (
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
            {/* DT-148 B8: aquí había `prose prose-sm prose-headings:… prose-p:…
                prose-a:…`, clases de @tailwindcss/typography — un plugin que
                este proyecto NO tiene instalado. Eran INERTES, y con el
                preflight de Tailwind borrando los márgenes por defecto la
                descripción se servía como un muro de texto. Estas selectoras
                de descendiente sí actúan sobre el HTML saneado de la oferta
                (las etiquetas son las de `sanitizeHtml`) y usan el tema. */}
            <div
              className={cn(
                "max-w-none text-sm leading-relaxed text-text-secondary",
                "[&_h1]:mt-5 [&_h1]:mb-2 [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:text-text-primary",
                "[&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:text-text-primary",
                "[&_h3]:mt-4 [&_h3]:mb-1 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-text-primary",
                "[&_h4]:mt-4 [&_h4]:mb-1 [&_h4]:text-sm [&_h4]:font-semibold [&_h4]:text-text-primary",
                "[&_p]:mt-3 [&_strong]:font-semibold [&_strong]:text-text-primary [&_em]:italic",
                "[&_ul]:mt-3 [&_ul]:ml-5 [&_ul]:list-disc [&_ol]:mt-3 [&_ol]:ml-5 [&_ol]:list-decimal",
                "[&_li]:mt-1 [&_blockquote]:mt-3 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3",
                "[&_a]:text-swiss-red [&_a]:underline [&_code]:font-mono [&_code]:text-xs",
                "[&_pre]:mt-3 [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-surface-secondary [&_pre]:p-3",
              )}
              dangerouslySetInnerHTML={{ __html: sanitizeHtml(job.description) }}
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
        {token && user && (
          <DocumentGenerator
            key={`${user.id}:${hash}`}
            jobHash={hash}
            jobTitle={job.title}
            jobCompany={job.company}
          />
        )}
      </main>
    </div>
  );
}

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

function scoreColor(value) {
  if (value > 70) return "bg-green-500";
  if (value > 40) return "bg-yellow-500";
  return "bg-red-500";
}

function scoreTextColor(value) {
  if (value > 70) return "text-green-700";
  if (value > 40) return "text-yellow-700";
  return "text-red-700";
}

function ScoreBar({ label, value }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 text-xs text-text-tertiary capitalize">{label}</span>
      <div className="h-2 flex-1 rounded-full bg-surface-tertiary">
        <div
          className={`h-2 rounded-full ${scoreColor(value)}`}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <span className="w-8 text-right text-xs text-text-secondary">
        {Math.round(value)}
      </span>
    </div>
  );
}

function formatSalary(min, max) {
  if (!min && !max) return null;
  const fmt = (v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v);
  if (min && max) return `${fmt(min)}–${fmt(max)} CHF`;
  if (min) return `ab ${fmt(min)} CHF`;
  return `bis ${fmt(max)} CHF`;
}

function MatchCard({ match, onFeedback, onClearFeedback, onImplicit }) {
  const sourceColor =
    SOURCE_COLORS[match.job_source] || "bg-gray-100 text-gray-700";
  const salary = formatSalary(match.job_salary_min, match.job_salary_max);
  const initial = match.job_company
    ? match.job_company[0].toUpperCase()
    : "?";

  function handleJobClick() {
    onImplicit?.({ jobHash: match.job_hash, action: "opened" });
  }

  function handleFeedback(feedback) {
    onFeedback?.({ jobHash: match.job_hash, feedback });
  }

  const scoreBgColor =
    match.score_final > 70
      ? "bg-success-light"
      : match.score_final > 40
        ? "bg-warning-light"
        : "bg-error-light";

  const scoreTxtColor =
    match.score_final > 70
      ? "text-success"
      : match.score_final > 40
        ? "text-warning"
        : "text-error";

  return (
    <div className="bg-surface rounded-xl shadow-card hover:shadow-card-hover p-5 transition-all duration-200">
      {/* Header: company initial + title + score */}
      <div className="flex gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-swiss-red-light text-lg font-bold text-swiss-red">
          {initial}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <Link
                to={`/job/${match.job_hash}`}
                onClick={handleJobClick}
                className="block truncate text-[15px] font-semibold text-text-primary hover:text-swiss-red"
                title={match.job_title_en ? match.job_title : undefined}
              >
                {match.job_title_en || match.job_title}
              </Link>
              {match.job_title_en && (
                <span
                  className="inline-flex items-center gap-0.5 text-[10px] text-text-tertiary"
                  title={`Original: ${match.job_title}`}
                >
                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                      d="M3 5h12M9 3v2m1.048 9.5A18.022 18.022 0 016.412 9m6.088 9h7M11 21l5-10 5 10M12.751 5C11.783 10.77 8.07 15.61 3 18.129" />
                  </svg>
                  Translated from {match.job_language?.toUpperCase()}
                </span>
              )}
              <p className="truncate text-sm text-text-secondary">
                {match.job_company}
                {match.job_location && ` · ${match.job_location}`}
              </p>
            </div>

            {/* Final score */}
            <div
              className={`flex w-14 h-14 shrink-0 items-center justify-center rounded-full ${scoreBgColor}`}
            >
              <span
                className={`text-lg font-bold ${scoreTxtColor}`}
              >
                {Math.round(match.score_final)}
              </span>
            </div>
          </div>

          {/* Source + salary badges */}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <span
              className="inline-block rounded-full px-2 py-0.5 text-[10px] font-medium bg-surface-tertiary text-text-secondary"
            >
              {match.job_source}
            </span>
            {salary && (
              <span className="text-sm font-semibold text-swiss-red">
                {salary}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Score breakdown */}
      <div className="mt-3 space-y-1">
        <ScoreBar label="Skills" value={match.scores.embedding * 100} />
        <ScoreBar label="Salary" value={match.scores.salary * 100} />
        <ScoreBar label="Location" value={match.scores.location * 100} />
        <ScoreBar label="Recency" value={match.scores.recency * 100} />
        {match.scores.llm > 0 && (
          <ScoreBar label="AI" value={match.scores.llm * 100} />
        )}
      </div>

      {/* Matching / Missing skills */}
      {(match.matching_skills.length > 0 ||
        match.missing_skills.length > 0) && (
        <div className="mt-3 flex flex-wrap gap-1">
          {match.matching_skills.map((skill) => (
            <span
              key={skill}
              className="inline-block rounded-full bg-success-light px-2 py-0.5 text-[10px] font-medium text-success"
            >
              {skill}
            </span>
          ))}
          {match.missing_skills.map((skill) => (
            <span
              key={skill}
              className="inline-block rounded-full bg-surface-tertiary px-2 py-0.5 text-[10px] font-medium text-text-tertiary line-through"
            >
              {skill}
            </span>
          ))}
        </div>
      )}

      {/* LLM explanation */}
      {match.explanation && (
        <p className="mt-2 text-xs leading-relaxed text-text-secondary">
          {match.explanation}
        </p>
      )}

      {/* Feedback buttons */}
      <div className="mt-3 flex items-center gap-2 border-t border-border-light pt-3">
        <button
          type="button"
          onClick={() =>
            match.feedback === "thumbs_up"
              ? onClearFeedback?.({ jobHash: match.job_hash })
              : handleFeedback("thumbs_up")
          }
          title={match.feedback === "thumbs_up" ? "Desmarcar" : "Guardar oferta"}
          className={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-200 ${
            match.feedback === "thumbs_up"
              ? "bg-success-light text-success ring-1 ring-success/30"
              : "bg-surface-secondary text-text-tertiary hover:scale-105"
          }`}
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"
            />
          </svg>
          Good
        </button>
        <button
          type="button"
          onClick={() =>
            match.feedback === "thumbs_down"
              ? onClearFeedback?.({ jobHash: match.job_hash })
              : handleFeedback("thumbs_down")
          }
          title={match.feedback === "thumbs_down" ? "Desmarcar" : "No me interesa"}
          className={`flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-200 ${
            match.feedback === "thumbs_down"
              ? "bg-error-light text-error ring-1 ring-error/30"
              : "bg-surface-secondary text-text-tertiary hover:scale-105"
          }`}
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018c.163 0 .326.02.485.06L17 4m-7 10v2a3 3 0 003 3h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h2m5-6h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5"
            />
          </svg>
          Not for me
        </button>
        <Link
          to={`/job/${match.job_hash}`}
          onClick={handleJobClick}
          className="ml-auto text-xs text-swiss-red font-medium hover:underline"
        >
          View details
        </Link>
      </div>
    </div>
  );
}

export default memo(MatchCard);

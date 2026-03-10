import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  useAnalyze,
  useMatchResults,
  useSubmitFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import { useProfile } from "../hooks/useProfile";
import MatchCard from "../components/MatchCard";

const PAGE_SIZE = 20;

export default function MatchPage() {
  const { data: profile, isLoading: profileLoading } = useProfile();
  const [limit, setLimit] = useState(PAGE_SIZE);
  const {
    data: results,
    isLoading: resultsLoading,
    isError,
    error,
  } = useMatchResults(limit, 0);
  const analyze = useAnalyze();
  const submitFeedback = useSubmitFeedback();
  const submitImplicit = useSubmitImplicit();

  // Track view_time on page unmount
  const enterTime = useRef(Date.now());
  const resultsRef = useRef(null);
  resultsRef.current = results;

  useEffect(() => {
    return () => {
      const duration = Date.now() - enterTime.current;
      if (resultsRef.current?.data?.length > 0) {
        submitImplicit.mutate({
          jobHash: resultsRef.current.data[0].job_hash,
          action: "view_time",
          durationMs: duration,
        });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasCvEmbedding = profile?.has_cv_embedding;
  const matches = results?.data ?? [];
  const total = results?.total ?? 0;
  const hasMore = matches.length < total;
  const isLoading = profileLoading || resultsLoading;

  function handleAnalyze() {
    setLimit(PAGE_SIZE);
    analyze.mutate();
  }

  function handleLoadMore() {
    setLimit((prev) => prev + PAGE_SIZE);
  }

  function handleFeedback({ jobHash, feedback }) {
    submitFeedback.mutate({ jobHash, feedback });
  }

  function handleImplicit({ jobHash, action, durationMs }) {
    submitImplicit.mutate({ jobHash, action, durationMs });
  }

  if (isLoading && matches.length === 0) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <div className="mx-auto max-w-3xl p-4 pb-20">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-bold text-text-primary tracking-tight">AI Matches</h1>
          <Link to="/" className="text-sm text-swiss-red font-medium hover:underline">
            Back to search
          </Link>
        </div>

        {/* CV warning */}
        {!hasCvEmbedding && (
          <div className="mb-4 bg-warning-light border border-warning/20 rounded-xl p-4">
            <p className="text-sm text-warning">
              Upload your CV to enable AI matching.{" "}
              <Link
                to="/profile"
                className="text-swiss-red font-medium underline"
              >
                Go to Profile
              </Link>
            </p>
          </div>
        )}

        {/* Analyze button */}
        <div className="mb-6">
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={!hasCvEmbedding || analyze.isPending}
            className="w-full bg-swiss-red hover:bg-swiss-red-hover rounded-xl px-4 py-3 text-base font-semibold text-white shadow-card hover:shadow-card-hover transition-all duration-200 disabled:opacity-50"
          >
            {analyze.isPending ? "Analyzing..." : "Find Matches"}
          </button>
          {analyze.isSuccess && (
            <p className="mt-2 text-center text-sm text-success font-medium">
              Found {analyze.data.results_count} matches from{" "}
              {analyze.data.total_candidates} candidates.
            </p>
          )}
          {analyze.isError && (
            <div className="mt-2 bg-error-light text-error rounded-xl p-3 text-sm">
              {analyze.error.message}
            </div>
          )}
        </div>

        {/* Results error */}
        {isError && (
          <div className="bg-error-light text-error rounded-xl p-4 text-sm">
            Error loading results: {error.message}
          </div>
        )}

        {/* Results count */}
        {!isError && matches.length > 0 && (
          <p className="mb-3 text-sm text-text-secondary font-medium">
            Showing {matches.length} of {total} match{total !== 1 ? "es" : ""}
          </p>
        )}

        {/* Results list */}
        <div className="space-y-3">
          {matches.map((match) => (
            <MatchCard
              key={match.id}
              match={match}
              onFeedback={handleFeedback}
              onImplicit={handleImplicit}
            />
          ))}
        </div>

        {/* Load more */}
        {hasMore && (
          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={handleLoadMore}
              disabled={resultsLoading}
              className="rounded-xl border border-border bg-surface px-6 py-2.5 text-sm font-medium text-text-secondary hover:bg-surface-hover transition-colors disabled:opacity-50"
            >
              {resultsLoading ? "Loading..." : `Load more (${total - matches.length} remaining)`}
            </button>
          </div>
        )}

        {/* Empty state */}
        {!isError && !resultsLoading && matches.length === 0 && (
          <div className="py-12 text-center">
            <p className="text-text-tertiary">
              {hasCvEmbedding
                ? 'No matches yet. Click "Find Matches" to start.'
                : "Upload your CV to get AI-powered job matches."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

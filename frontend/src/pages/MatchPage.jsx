import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import {
  useAnalyze,
  useMatchResults,
  useSubmitFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import { useProfile } from "../hooks/useProfile";
import MatchCard from "../components/MatchCard";

export default function MatchPage() {
  const { data: profile, isLoading: profileLoading } = useProfile();
  const {
    data: results,
    isLoading: resultsLoading,
    isError,
    error,
  } = useMatchResults();
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
  const isLoading = profileLoading || resultsLoading;

  function handleAnalyze() {
    analyze.mutate(20);
  }

  function handleFeedback({ jobHash, feedback }) {
    submitFeedback.mutate({ jobHash, feedback });
  }

  function handleImplicit({ jobHash, action, durationMs }) {
    submitImplicit.mutate({ jobHash, action, durationMs });
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-300 border-t-gray-900" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-2xl p-4 pb-20">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <h1 className="text-2xl font-bold text-gray-900">AI Matches</h1>
          <Link to="/" className="text-sm text-blue-600 hover:underline">
            Back to search
          </Link>
        </div>

        {/* CV warning */}
        {!hasCvEmbedding && (
          <div className="mb-4 rounded-lg border border-yellow-200 bg-yellow-50 p-4">
            <p className="text-sm text-yellow-800">
              Upload your CV to enable AI matching.{" "}
              <Link
                to="/profile"
                className="font-medium text-yellow-900 underline"
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
            className="w-full rounded-lg bg-gray-900 px-4 py-3 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {analyze.isPending ? "Analyzing..." : "Find Matches"}
          </button>
          {analyze.isSuccess && (
            <p className="mt-2 text-center text-sm text-green-600">
              Found {analyze.data.results_count} matches from{" "}
              {analyze.data.total_candidates} candidates.
            </p>
          )}
          {analyze.isError && (
            <div className="mt-2 rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {analyze.error.message}
            </div>
          )}
        </div>

        {/* Results error */}
        {isError && (
          <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
            Error loading results: {error.message}
          </div>
        )}

        {/* Results count */}
        {!isError && matches.length > 0 && (
          <p className="mb-3 text-xs text-gray-500">
            {total} match{total !== 1 ? "es" : ""}
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

        {/* Empty state */}
        {!isError && !resultsLoading && matches.length === 0 && (
          <div className="py-12 text-center">
            <p className="text-gray-500">
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

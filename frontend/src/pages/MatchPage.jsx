import { useEffect, useMemo, useRef, useState } from "react";
import {
  Sparkles,
  AlertTriangle,
  ArrowRight,
  Inbox,
} from "lucide-react";
import {
  useAnalyze,
  useMatchResults,
  useSubmitFeedback,
  useClearFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import { useProfile } from "../hooks/useProfile";
import MatchCard from "../components/MatchCard";
import CategoryTabs from "../components/CategoryTabs";
import {
  CATEGORIES,
  CATEGORY_MAP,
  groupByCategory,
} from "../utils/jobCategories";
import {
  Button,
  LinkButton,
  PageHeader,
  EmptyState,
  MetricTile,
  SkeletonCard,
  cn,
} from "../components/ui";

export default function MatchPage() {
  const { data: profile, isLoading: profileLoading } = useProfile();
  const {
    data: results,
    isLoading: resultsLoading,
    isError,
    error,
  } = useMatchResults(3000, 0);

  const analyze = useAnalyze();
  const submitFeedback = useSubmitFeedback();
  const clearFeedback = useClearFeedback();
  const submitImplicit = useSubmitImplicit();

  const [activeCategory, setActiveCategory] = useState(null);

  const enterTime = useRef(Date.now());
  const resultsRef = useRef(results);
  resultsRef.current = results;

  // Implicit view_time on unmount
  useEffect(() => {
    const enteredAt = enterTime.current;
    return () => {
      const duration = Date.now() - enteredAt;
      const first = resultsRef.current?.data?.[0];
      if (first) {
        submitImplicit.mutate({
          jobHash: first.job_hash,
          action: "view_time",
          durationMs: duration,
        });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasCvEmbedding = profile?.has_cv_embedding;
  const allMatches = useMemo(() => results?.data ?? [], [results?.data]);
  const total = results?.total ?? 0;
  const isLoading = profileLoading || resultsLoading;

  const grouped = useMemo(() => groupByCategory(allMatches), [allMatches]);

  const tabItems = useMemo(() => {
    const items = CATEGORIES.filter(
      (cat) => (grouped.get(cat.id) ?? []).length > 0,
    ).map((cat) => ({
      id: cat.id,
      label: cat.shortLabel || cat.label,
      shortLabel: cat.shortLabel,
      count: (grouped.get(cat.id) ?? []).length,
    }));
    const otrosCount = (grouped.get("otros") ?? []).length;
    if (otrosCount > 0) {
      items.push({ id: "otros", label: "Other", count: otrosCount });
    }
    return items;
  }, [grouped]);

  const visibleMatches = activeCategory
    ? grouped.get(activeCategory) ?? []
    : [];

  // Métricas: top score y matches > 70
  const topScore = useMemo(
    () =>
      allMatches.length > 0
        ? Math.round(Math.max(...allMatches.map((m) => m.score_final)))
        : 0,
    [allMatches],
  );
  const strongMatches = useMemo(
    () => allMatches.filter((m) => m.score_final >= 70).length,
    [allMatches],
  );

  function handleFeedback({ jobHash, feedback }) {
    submitFeedback.mutate({ jobHash, feedback });
  }
  function handleClearFeedback({ jobHash }) {
    clearFeedback.mutate({ jobHash });
  }
  function handleImplicit({ jobHash, action, durationMs }) {
    submitImplicit.mutate({ jobHash, action, durationMs });
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="AI Matching"
        title="Your matches"
        description="Curated jobs ranked against your CV using semantic search, salary fit and recency."
        actions={
          <Button
            variant="primary"
            size="lg"
            leftIcon={<Sparkles className="h-4 w-4" />}
            loading={analyze.isPending}
            disabled={!hasCvEmbedding || analyze.isPending}
            onClick={() => {
              setActiveCategory(null);
              analyze.mutate();
            }}
          >
            {analyze.isPending ? "Analyzing" : "Find new matches"}
          </Button>
        }
      />

      {/* CV warning */}
      {!profileLoading && !hasCvEmbedding && (
        <div
          className={cn(
            "mt-6 flex items-start gap-3 rounded-xl border border-warning-border bg-warning-light p-4",
          )}
        >
          <AlertTriangle className="h-5 w-5 shrink-0 text-warning" aria-hidden="true" />
          <div className="flex-1">
            <p className="text-sm font-medium text-text-primary">
              Upload your CV to unlock AI matching
            </p>
            <p className="mt-0.5 text-sm text-text-secondary">
              Without an embedded CV, we can't compute semantic scores for jobs.
            </p>
          </div>
          <LinkButton
            to="/profile"
            variant="outline"
            size="sm"
            rightIcon={<ArrowRight className="h-3.5 w-3.5" />}
            className="shrink-0"
          >
            Go to Profile
          </LinkButton>
        </div>
      )}

      {/* Estado del análisis */}
      {analyze.isSuccess && (
        <p className="mt-3 text-sm text-success">
          Found {analyze.data.results_count} new matches from{" "}
          {analyze.data.total_candidates} candidates.
        </p>
      )}
      {analyze.isError && (
        <div className="mt-3 rounded-xl border border-error-border bg-error-light p-3 text-sm text-error">
          {analyze.error?.message || "Analysis failed"}
        </div>
      )}

      {/* Métricas */}
      {allMatches.length > 0 && (
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricTile label="Total matches" value={total.toLocaleString()} />
          <MetricTile label="Strong (≥70)" value={strongMatches} tone="success" />
          <MetricTile label="Top score" value={topScore} tone="ink" />
          <MetricTile label="Categories" value={tabItems.length} />
        </div>
      )}

      {/* Loading */}
      {isLoading && allMatches.length === 0 && (
        <div className="mt-6 space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="mt-6 rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
          Error loading results: {error?.message}
        </div>
      )}

      {/* Sin resultados */}
      {!isLoading && !isError && allMatches.length === 0 && (
        <div className="mt-6">
          <EmptyState
            icon={Sparkles}
            title={hasCvEmbedding ? "No matches yet" : "Add your CV first"}
            description={
              hasCvEmbedding
                ? 'Click "Find new matches" to analyze your CV against the latest jobs.'
                : "Upload your CV in your profile to get AI-powered job recommendations."
            }
            action={
              hasCvEmbedding ? (
                <Button
                  variant="primary"
                  leftIcon={<Sparkles className="h-4 w-4" />}
                  loading={analyze.isPending}
                  onClick={() => analyze.mutate()}
                >
                  Find matches
                </Button>
              ) : (
                <LinkButton to="/profile" variant="primary">
                  Go to Profile
                </LinkButton>
              )
            }
          />
        </div>
      )}

      {/* Categorías */}
      {allMatches.length > 0 && (
        <section className="mt-6">
          <div className="mb-3 flex items-end justify-between gap-3">
            <h2 className="text-sm font-medium text-text-secondary">
              Browse by category
            </h2>
            {activeCategory && (
              <button
                type="button"
                onClick={() => setActiveCategory(null)}
                className="text-xs font-medium text-text-tertiary hover:text-text-primary"
              >
                Show all categories
              </button>
            )}
          </div>
          <CategoryTabs
            categories={tabItems}
            activeId={activeCategory}
            onChange={setActiveCategory}
          />
        </section>
      )}

      {/* Resultados de la categoría */}
      {activeCategory && (
        <section className="mt-6">
          <header className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold tracking-tight text-text-primary">
              {CATEGORY_MAP[activeCategory]?.label ?? "Other"}{" "}
              <span className="text-text-tertiary font-normal">
                · {visibleMatches.length}
              </span>
            </h2>
          </header>
          <div className="space-y-3">
            {visibleMatches.map((match) => (
              <MatchCard
                key={match.id}
                match={match}
                onFeedback={handleFeedback}
                onClearFeedback={handleClearFeedback}
                onImplicit={handleImplicit}
              />
            ))}
          </div>
        </section>
      )}

      {allMatches.length > 0 && !activeCategory && (
        <div className="mt-8 flex flex-col items-center gap-2 rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
          <Inbox className="h-5 w-5 text-text-quaternary" aria-hidden="true" />
          <p className="text-sm text-text-secondary">
            Pick a category above to view its offers.
          </p>
        </div>
      )}
    </div>
  );
}

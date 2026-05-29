import { useMemo, useState } from "react";
import { Bookmark, Sparkles } from "lucide-react";
import {
  useSavedJobs,
  useSubmitFeedback,
  useClearFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import MatchCard from "../components/MatchCard";
import CategoryTabs from "../components/CategoryTabs";
import {
  CATEGORIES,
  CATEGORY_MAP,
  groupByCategory,
} from "../utils/jobCategories";
import {
  EmptyState,
  LinkButton,
  PageHeader,
  SkeletonCard,
} from "../components/ui";

export default function SavedJobsPage() {
  const { data: results, isLoading, isError, error } = useSavedJobs();
  const submitFeedback = useSubmitFeedback();
  const clearFeedback = useClearFeedback();
  const submitImplicit = useSubmitImplicit();

  const [activeCategory, setActiveCategory] = useState(null);

  const allSaved = useMemo(() => results?.data ?? [], [results?.data]);
  const total = results?.total ?? 0;

  const grouped = useMemo(() => groupByCategory(allSaved), [allSaved]);

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
        eyebrow="Bookmarks"
        title="Saved jobs"
        description={
          total > 0
            ? `${total} match${total !== 1 ? "es" : ""} you marked as relevant.`
            : "Anything you mark as a good match will show up here."
        }
        actions={
          <LinkButton to="/match" variant="secondary" leftIcon={<Sparkles className="h-4 w-4" />}>
            Back to matches
          </LinkButton>
        }
      />

      {/* Loading */}
      {isLoading && (
        <div className="mt-6 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="mt-6 rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
          Error loading saved jobs: {error?.message}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !isError && allSaved.length === 0 && (
        <div className="mt-6">
          <EmptyState
            icon={Bookmark}
            title="No saved jobs yet"
            description="Tap Good on any match in the Matches page to keep it here for later."
            action={
              <LinkButton to="/match" variant="primary" leftIcon={<Sparkles className="h-4 w-4" />}>
                Go to Matches
              </LinkButton>
            }
          />
        </div>
      )}

      {/* Categorías */}
      {!isLoading && !isError && allSaved.length > 0 && (
        <>
          <section className="mt-6">
            <h2 className="mb-3 text-sm font-medium text-text-secondary">
              Filter by category
            </h2>
            <CategoryTabs
              categories={tabItems}
              activeId={activeCategory}
              onChange={setActiveCategory}
            />
          </section>

          {activeCategory ? (
            <section className="mt-6">
              <header className="mb-3 flex items-center justify-between">
                <h2 className="text-base font-semibold tracking-tight text-text-primary">
                  {CATEGORY_MAP[activeCategory]?.label ?? "Other"}{" "}
                  <span className="font-normal text-text-tertiary">
                    · {visibleMatches.length}
                  </span>
                </h2>
                <button
                  type="button"
                  onClick={() => setActiveCategory(null)}
                  className="text-xs font-medium text-text-tertiary hover:text-text-primary"
                >
                  Show all
                </button>
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
          ) : (
            <div className="mt-6 flex flex-col items-center gap-2 rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center">
              <Bookmark className="h-5 w-5 text-text-quaternary" aria-hidden="true" />
              <p className="text-sm text-text-secondary">
                Pick a category above to view your saved offers.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

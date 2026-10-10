import { useCallback, useMemo, useState } from "react";
import {
  Sparkles,
  AlertTriangle,
  ArrowRight,
  Inbox,
} from "lucide-react";
import {
  useAnalyze,
  useMatchFacets,
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
  const [activeCategory, setActiveCategory] = useState(null);
  const [categoryOffset, setCategoryOffset] = useState(0);
  const facetsQuery = useMatchFacets();
  const facetsEnabled = facetsQuery.isSuccess;
  const legacyMode = facetsQuery.isError;
  const {
    data: results,
    isLoading: resultsLoading,
    isError,
    error,
  } = useMatchResults(
    facetsEnabled ? 50 : 3000,
    facetsEnabled ? categoryOffset : 0,
    facetsEnabled ? activeCategory : null,
    legacyMode || (facetsEnabled && Boolean(activeCategory)),
  );

  const analyze = useAnalyze();
  const submitFeedback = useSubmitFeedback();
  const clearFeedback = useClearFeedback();
  const submitImplicit = useSubmitImplicit();

  // H12/T7 — RETIRADA la señal implícita `view_time`.
  //
  // Medía el tiempo que la PÁGINA estuvo abierta y se lo atribuía a
  // `data[0]`: la primera oferta del lote, que el usuario puede no haber
  // visto nunca (las tarjetas se pintan por categoría, y la pestaña inicial
  // ni siquiera es la primera). Era una señal falsa alimentando el ranking,
  // y una señal falsa es peor que ninguna.
  //
  // Medirlo de verdad exige `IntersectionObserver` por tarjeta: eso es una
  // funcionalidad nueva, no el arreglo de un defecto, y se decide aparte.

  const hasCvEmbedding = profile?.has_cv_embedding;
  const facets = facetsQuery.data;
  const allMatches = useMemo(() => results?.data ?? [], [results?.data]);
  const total = facetsEnabled ? facets.total : (results?.total ?? 0);
  const hasMatches = total > 0;
  const isLoading =
    profileLoading ||
    facetsQuery.isLoading ||
    (resultsLoading && (legacyMode || Boolean(activeCategory)));

  const grouped = useMemo(() => groupByCategory(allMatches), [allMatches]);

  const watchlistMatches = useMemo(
    () =>
      allMatches
        .filter((m) => m.school_id)
        .sort(
          (a, b) =>
            b.score_final + (b.urgency_score || 0) -
            (a.score_final + (a.urgency_score || 0)),
        ),
    [allMatches],
  );

  const tabItems = useMemo(() => {
    if (facetsEnabled) {
      const items = [];
      if (facets.watchlist_count > 0) {
        items.push({
          id: "__watchlist",
          label: "Watchlist",
          fullLabel: "Watchlist",
          count: facets.watchlist_count,
        });
      }
      items.push(
        ...facets.categories
          .filter((category) => category.count > 0)
          .map((category) => ({
            id: category.id,
            label: category.short_label || category.label,
            fullLabel: category.label,
            shortLabel: category.short_label,
            count: category.count,
          })),
      );
      return items;
    }
    const items = [];
    if (watchlistMatches.length > 0) {
      items.push({
        id: "__watchlist",
        label: "Watchlist",
        shortLabel: "Watchlist",
        count: watchlistMatches.length,
      });
    }
    items.push(
      ...CATEGORIES.filter(
        (cat) => (grouped.get(cat.id) ?? []).length > 0,
      ).map((cat) => ({
        id: cat.id,
        label: cat.shortLabel || cat.label,
        shortLabel: cat.shortLabel,
        count: (grouped.get(cat.id) ?? []).length,
      })),
    );
    const otrosCount = (grouped.get("otros") ?? []).length;
    if (otrosCount > 0) {
      items.push({ id: "otros", label: "Other", count: otrosCount });
    }
    return items;
  }, [facets, facetsEnabled, grouped, watchlistMatches.length]);

  const activeTab = tabItems.find((item) => item.id === activeCategory);
  const visibleMatches = facetsEnabled
    ? allMatches
    : activeCategory === "__watchlist"
      ? watchlistMatches
      : activeCategory
        ? grouped.get(activeCategory) ?? []
        : [];

  const topScore = facetsEnabled
    ? Math.round(facets.top_score)
    : allMatches.length > 0
      ? Math.round(Math.max(...allMatches.map((match) => match.score_final)))
      : 0;
  const strongMatches = facetsEnabled
    ? facets.strong_count
    : allMatches.filter((match) => match.score_final >= 70).length;

  // L4/T7: estos tres se recreaban en CADA render, así que cambiaban de
  // identidad y el `memo(MatchCard)` no servía de nada: se repintaban las
  // 1.800 tarjetas por cualquier cambio de estado de la página.
  // `mutate` de react-query es estable entre renders; se extrae para que la
  // dependencia sea la función y no una expresión de miembro, que es lo que
  // el plugin de React no sabe memoizar.
  const { mutate: mutarFeedback } = submitFeedback;
  const { mutate: mutarBorradoFeedback } = clearFeedback;
  const { mutate: mutarImplicito } = submitImplicit;

  const handleFeedback = useCallback(
    ({ jobHash, feedback }) => mutarFeedback({ jobHash, feedback }),
    [mutarFeedback],
  );
  const handleClearFeedback = useCallback(
    ({ jobHash }) => mutarBorradoFeedback({ jobHash }),
    [mutarBorradoFeedback],
  );
  const handleImplicit = useCallback(
    ({ jobHash, action, durationMs }) =>
      mutarImplicito({ jobHash, action, durationMs }),
    [mutarImplicito],
  );

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
              setCategoryOffset(0);
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
      {hasMatches && (
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
      {!isLoading && !isError && !hasMatches && (
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
      {hasMatches && (
        <section className="mt-6">
          <div className="mb-3 flex items-end justify-between gap-3">
            <h2 className="text-sm font-medium text-text-secondary">
              Browse by category
            </h2>
            {activeCategory && (
              <button
                type="button"
                onClick={() => {
                  setActiveCategory(null);
                  setCategoryOffset(0);
                }}
                className="text-xs font-medium text-text-tertiary hover:text-text-primary"
              >
                Show all categories
              </button>
            )}
          </div>
          <CategoryTabs
            categories={tabItems}
            activeId={activeCategory}
            onChange={(category) => {
              setActiveCategory(category);
              setCategoryOffset(0);
            }}
          />
        </section>
      )}

      {/* Resultados de la categoría */}
      {activeCategory && (
        <section className="mt-6">
          <header className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold tracking-tight text-text-primary">
              {activeTab?.fullLabel ??
                activeTab?.label ??
                CATEGORY_MAP[activeCategory]?.label ??
                "Other"}{" "}
              <span className="text-text-tertiary font-normal">
                · {activeTab?.count ?? visibleMatches.length}
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
          {facetsEnabled &&
            (categoryOffset > 0 ||
              categoryOffset + visibleMatches.length <
                (activeTab?.count ?? 0)) && (
            <div className="mt-4 flex justify-center gap-3">
              <Button
                variant="outline"
                disabled={categoryOffset === 0}
                onClick={() => setCategoryOffset((offset) => Math.max(0, offset - 50))}
              >
                Previous
              </Button>
              <Button
                variant="outline"
                disabled={categoryOffset + visibleMatches.length >= (activeTab?.count ?? 0)}
                onClick={() => setCategoryOffset((offset) => offset + 50)}
              >
                Next
              </Button>
            </div>
          )}
        </section>
      )}

      {hasMatches && !activeCategory && (
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

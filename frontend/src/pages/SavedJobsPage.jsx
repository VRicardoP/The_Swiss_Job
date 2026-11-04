import { useCallback, useMemo, useState } from "react";
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
  // A-35 — el tope era 100 y la página lo llamaba SIN argumentos, así que con
  // 140 guardados la cabecera leía «140 matches you marked as relevant» (el
  // `total` del servidor) y la suma de las pestañas de categoría daba 100: las
  // 40 restantes eran inalcanzables y nada lo decía.
  //
  // Se sube a 500, el mismo tope que `WatchlistPage` ya usa, en vez de montar
  // un paginador. El motivo no es pereza: las categorías y sus contadores se
  // calculan EN CLIENTE sobre lo cargado (`groupByCategory(allSaved)`), así que
  // paginar en el servidor haría que las pestañas cambiaran de significado en
  // cada página — cambiaría una inconsistencia por otra peor. 500 marcadores
  // cubre el uso real con holgura.
  //
  // Y por si algún día no basta, debajo hay un aviso cuando `total` supera lo
  // servido: el número de la cabecera no puede volver a mentir en silencio.
  const { data: results, isLoading, isError, error } = useSavedJobs(500);
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

  // DT-148 A17: funciones nuevas en cada render ⇒ el `memo(MatchCard)` que ya
  // se paga no filtraba nada. `mutate` es estable entre renders.
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

      {/* A-35: si el servidor dice que hay más de lo que se ha traído, se DICE.
          Un contador que no cuadra con lo que se ve es peor que un contador
          menor. */}
      {total > allSaved.length && (
        <p
          role="status"
          className="mt-3 rounded-lg border border-warning-border bg-warning-light px-3 py-2 text-sm text-text-secondary"
        >
          Showing the first {allSaved.length} of {total} saved jobs.
        </p>
      )}

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

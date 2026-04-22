import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useSavedJobs,
  useSubmitFeedback,
  useClearFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import MatchCard from "../components/MatchCard";
import {
  CATEGORIES,
  CATEGORY_MAP,
  groupByCategory,
} from "../utils/jobCategories";

export default function SavedJobsPage() {
  const { data: results, isLoading, isError, error } = useSavedJobs();
  const submitFeedback = useSubmitFeedback();
  const clearFeedback = useClearFeedback();
  const submitImplicit = useSubmitImplicit();

  const [activeCategory, setActiveCategory] = useState(null);

  const allSaved = results?.data ?? [];
  const total = results?.total ?? 0;

  const grouped = groupByCategory(allSaved);
  const availableCategories = CATEGORIES.filter(
    (cat) => (grouped.get(cat.id) ?? []).length > 0
  );
  const otrosCount = (grouped.get("otros") ?? []).length;
  const visibleMatches = activeCategory
    ? (grouped.get(activeCategory) ?? [])
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

  if (isLoading) {
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
          <div>
            <h1 className="text-2xl font-bold text-text-primary tracking-tight">
              Saved Jobs
            </h1>
            {total > 0 && (
              <p className="text-sm text-text-tertiary mt-0.5">
                {total} oferta{total !== 1 ? "s" : ""} guardada{total !== 1 ? "s" : ""}
              </p>
            )}
          </div>
          <Link
            to="/match"
            className="text-sm text-swiss-red font-medium hover:underline"
          >
            ← Volver a Matches
          </Link>
        </div>

        {/* Error */}
        {isError && (
          <div className="bg-error-light text-error rounded-xl p-4 text-sm">
            Error loading saved jobs: {error.message}
          </div>
        )}

        {/* Sin guardados */}
        {!isError && allSaved.length === 0 && (
          <div className="py-16 text-center">
            <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-surface-tertiary">
              <svg
                className="h-8 w-8 text-text-tertiary"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5"
                />
              </svg>
            </div>
            <p className="text-text-secondary font-medium">No tienes ofertas guardadas</p>
            <p className="mt-1 text-sm text-text-tertiary">
              Pulsa <span className="font-semibold">Good</span> en cualquier oferta del análisis de matches para guardarla aquí.
            </p>
            <Link
              to="/match"
              className="mt-4 inline-block rounded-xl bg-swiss-red px-5 py-2.5 text-sm font-semibold text-white hover:bg-swiss-red-hover transition-colors"
            >
              Ir a Matches
            </Link>
          </div>
        )}

        {/* Category filter buttons */}
        {allSaved.length > 0 && (
          <div className="mb-6">
            <p className="mb-2 text-xs text-text-tertiary font-medium uppercase tracking-wide">
              Filtrar por categoría
            </p>
            <div className="flex flex-wrap gap-2">
              {availableCategories.map((cat) => {
                const count = (grouped.get(cat.id) ?? []).length;
                const isActive = activeCategory === cat.id;
                return (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() =>
                      setActiveCategory(isActive ? null : cat.id)
                    }
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-all duration-200 ${
                      isActive
                        ? "bg-swiss-red text-white shadow-sm"
                        : "bg-surface border border-border text-text-secondary hover:border-swiss-red/40 hover:text-swiss-red"
                    }`}
                  >
                    <span className="font-bold">{cat.id}</span>
                    <span className="hidden sm:inline">{cat.shortLabel}</span>
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                        isActive
                          ? "bg-white/20 text-white"
                          : "bg-surface-tertiary text-text-tertiary"
                      }`}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
              {otrosCount > 0 && (
                <button
                  type="button"
                  onClick={() =>
                    setActiveCategory(
                      activeCategory === "otros" ? null : "otros"
                    )
                  }
                  className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-all duration-200 ${
                    activeCategory === "otros"
                      ? "bg-swiss-red text-white shadow-sm"
                      : "bg-surface border border-border text-text-secondary hover:border-swiss-red/40 hover:text-swiss-red"
                  }`}
                >
                  <span>Otros</span>
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                      activeCategory === "otros"
                        ? "bg-white/20 text-white"
                        : "bg-surface-tertiary text-text-tertiary"
                    }`}
                  >
                    {otrosCount}
                  </span>
                </button>
              )}
            </div>
          </div>
        )}

        {/* Resultados de la categoría activa */}
        {activeCategory && (
          <>
            <div className="mb-3">
              <p className="text-sm text-text-secondary font-medium">
                <span className="font-bold text-text-primary">
                  {CATEGORY_MAP[activeCategory]?.label ?? "Otros"}
                </span>{" "}
                — {visibleMatches.length} oferta
                {visibleMatches.length !== 1 ? "s" : ""}
              </p>
            </div>
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
          </>
        )}

        {/* Sin categoría seleccionada */}
        {allSaved.length > 0 && !activeCategory && (
          <div className="py-10 text-center">
            <p className="text-text-tertiary text-sm">
              Selecciona una categoría para ver tus ofertas guardadas.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

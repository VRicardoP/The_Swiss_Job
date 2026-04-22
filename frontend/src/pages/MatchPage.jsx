import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  useAnalyze,
  useMatchResults,
  useSubmitFeedback,
  useClearFeedback,
  useSubmitImplicit,
} from "../hooks/useMatch";
import { useProfile } from "../hooks/useProfile";
import MatchCard from "../components/MatchCard";
import {
  CATEGORIES,
  CATEGORY_MAP,
  groupByCategory,
} from "../utils/jobCategories";

export default function MatchPage() {
  const { data: profile, isLoading: profileLoading } = useProfile();

  // Carga masiva sin traducciones — para categorizar y mostrar las tarjetas.
  // translate=false evita las llamadas a Groq LLM y reduce el tiempo de carga
  // de potencialmente 30-60s (caché fría) a menos de 2s.
  const { data: results, isLoading: resultsLoading, isError, error } =
    useMatchResults(3000, 0);

  const analyze = useAnalyze();
  const submitFeedback = useSubmitFeedback();
  const clearFeedback = useClearFeedback();
  const submitImplicit = useSubmitImplicit();

  // Categoría activa seleccionada (null = no mostrar resultados todavía)
  const [activeCategory, setActiveCategory] = useState(null);

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
  const allMatches = results?.data ?? [];
  const total = results?.total ?? 0;
  const isLoading = profileLoading || resultsLoading;

  // Agrupar todos los resultados por categoría
  const grouped = groupByCategory(allMatches);

  // Calcular categorías con resultados (para mostrar los botones)
  const availableCategories = CATEGORIES.filter(
    (cat) => (grouped.get(cat.id) ?? []).length > 0
  );
  const otrosCount = (grouped.get("otros") ?? []).length;

  // Resultados de la categoría activa
  const visibleMatches = activeCategory
    ? (grouped.get(activeCategory) ?? [])
    : [];

  function handleAnalyze() {
    setActiveCategory(null);
    analyze.mutate();
  }

  function handleFeedback({ jobHash, feedback }) {
    submitFeedback.mutate({ jobHash, feedback });
  }

  function handleClearFeedback({ jobHash }) {
    clearFeedback.mutate({ jobHash });
  }

  function handleImplicit({ jobHash, action, durationMs }) {
    submitImplicit.mutate({ jobHash, action, durationMs });
  }

  if (isLoading && allMatches.length === 0) {
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
              <Link to="/profile" className="text-swiss-red font-medium underline">
                Go to Profile
              </Link>
            </p>
          </div>
        )}

        {/* Analyze button */}
        <div className="mb-4">
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

        {/* Category filter buttons — solo cuando hay resultados */}
        {allMatches.length > 0 && (
          <div className="mb-6">
            <p className="mb-2 text-xs text-text-tertiary font-medium uppercase tracking-wide">
              Selecciona una categoría para ver los resultados
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

        {/* Results error */}
        {isError && (
          <div className="bg-error-light text-error rounded-xl p-4 text-sm">
            Error loading results: {error.message}
          </div>
        )}

        {/* Resultados de la categoría seleccionada */}
        {activeCategory && (
          <>
            <div className="mb-3 flex items-center justify-between">
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

        {/* Estado vacío: sin categoría seleccionada */}
        {!isError && !resultsLoading && allMatches.length > 0 && !activeCategory && (
          <div className="py-10 text-center">
            <p className="text-text-tertiary text-sm">
              Pulsa una categoría para ver las ofertas correspondientes.
            </p>
          </div>
        )}

        {/* Estado vacío: sin resultados */}
        {!isError && !resultsLoading && allMatches.length === 0 && (
          <div className="py-12 text-center">
            <p className="text-text-tertiary">
              {hasCvEmbedding
                ? 'No matches yet. Click "Find Matches" to start.'
                : "Upload your CV to get AI-powered job matches."}
            </p>
          </div>
        )}

        {/* Total general (informativo) */}
        {allMatches.length > 0 && (
          <p className="mt-6 text-center text-xs text-text-tertiary">
            {total} ofertas analizadas en total
          </p>
        )}
      </div>
    </div>
  );
}

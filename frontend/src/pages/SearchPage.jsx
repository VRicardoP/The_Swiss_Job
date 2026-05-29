import { useCallback, useEffect, useMemo, useRef } from "react";
import { Search, SlidersHorizontal, X, Briefcase } from "lucide-react";
import { useSearchStore } from "../stores/searchStore";
import { useJobSearch } from "../hooks/useJobSearch";
import JobCard from "../components/JobCard";
import FilterPanel from "../components/FilterPanel";
import {
  Badge,
  Button,
  EmptyState,
  Select,
  SkeletonCard,
  cn,
} from "../components/ui";

function useDebounce(callback, delay) {
  const timer = useRef(null);
  return useCallback(
    (value) => {
      clearTimeout(timer.current);
      timer.current = setTimeout(() => callback(value), delay);
    },
    [callback, delay],
  );
}

// Definición de chips de filtros activos (etiqueta legible + acción de borrado)
function useActiveFilterChips() {
  const s = useSearchStore();
  const chips = [];
  if (s.source)
    chips.push({ key: "source", label: `Source: ${s.source}`, clear: () => s.setSource("") });
  if (s.canton)
    chips.push({ key: "canton", label: `Canton: ${s.canton}`, clear: () => s.setCanton("") });
  if (s.language)
    chips.push({
      key: "language",
      label: `Lang: ${s.language.toUpperCase()}`,
      clear: () => s.setLanguage(""),
    });
  if (s.seniority)
    chips.push({
      key: "seniority",
      label: `Level: ${s.seniority}`,
      clear: () => s.setSeniority(""),
    });
  if (s.contractType)
    chips.push({
      key: "contract",
      label: `Contract: ${s.contractType.replace("_", " ")}`,
      clear: () => s.setContractType(""),
    });
  if (s.remoteOnly)
    chips.push({ key: "remote", label: "Remote only", clear: () => s.setRemoteOnly(false) });
  if (s.salaryMin)
    chips.push({
      key: "salaryMin",
      label: `Min ${s.salaryMin} CHF`,
      clear: () => s.setSalaryMin(""),
    });
  if (s.salaryMax)
    chips.push({
      key: "salaryMax",
      label: `Max ${s.salaryMax} CHF`,
      clear: () => s.setSalaryMax(""),
    });
  return chips;
}

export default function SearchPage() {
  const { q, sort, setQ, setSort, setFiltersOpen, resetFilters } = useSearchStore();
  const chips = useActiveFilterChips();

  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    error,
  } = useJobSearch();

  const sentinelRef = useRef(null);
  const debouncedSetQ = useDebounce(setQ, 300);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const jobs = useMemo(
    () => data?.pages.flatMap((page) => page.data) ?? [],
    [data?.pages],
  );
  const total = data?.pages[0]?.total ?? 0;
  const hasActiveFilters = chips.length > 0;

  return (
    <div>
      {/* Command bar (sticky) */}
      <div className="sticky top-14 z-30 border-b border-border bg-surface/85 backdrop-blur-lg sm:top-16">
        <div className="mx-auto max-w-4xl px-4 py-3 sm:px-6 sm:py-4">
          <div className="flex items-stretch gap-2">
            <label className="relative flex-1">
              <span className="sr-only">Search jobs</span>
              <Search
                className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary"
                aria-hidden="true"
              />
              <input
                type="text"
                defaultValue={q}
                onChange={(e) => debouncedSetQ(e.target.value)}
                placeholder="Search jobs, skills or companies"
                className={cn(
                  "h-11 w-full rounded-lg border border-border bg-surface pl-10 pr-3 text-sm",
                  "placeholder:text-text-quaternary text-text-primary",
                  "transition-all duration-150",
                  "focus:outline-none focus:border-ink focus:shadow-ring",
                )}
              />
            </label>

            <Select
              size="lg"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              fullWidth={false}
              aria-label="Sort"
              className="hidden w-44 sm:block"
              containerClassName="hidden sm:flex"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="salary">Salary</option>
              <option value="relevance">Relevance</option>
            </Select>

            <Button
              variant="secondary"
              size="lg"
              leftIcon={<SlidersHorizontal className="h-4 w-4" />}
              onClick={() => setFiltersOpen(true)}
            >
              <span className="hidden sm:inline">Filters</span>
              {hasActiveFilters && (
                <Badge variant="solid" size="xs" className="ml-1">
                  {chips.length}
                </Badge>
              )}
            </Button>
          </div>

          {/* Filtros activos + total */}
          {(hasActiveFilters || !isLoading) && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {!isLoading && (
                <span className="text-xs font-medium text-text-tertiary tabular-nums">
                  {total.toLocaleString()} job{total !== 1 ? "s" : ""}
                </span>
              )}

              {hasActiveFilters && (
                <>
                  <span className="hidden h-3.5 w-px bg-border sm:inline" aria-hidden="true" />
                  {chips.map((chip) => (
                    <button
                      key={chip.key}
                      type="button"
                      onClick={chip.clear}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full border border-border bg-surface",
                        "h-7 pl-2.5 pr-1.5 text-xs font-medium text-text-secondary",
                        "transition-colors hover:border-ink hover:text-text-primary",
                      )}
                    >
                      {chip.label}
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  ))}
                  <button
                    type="button"
                    onClick={resetFilters}
                    className="text-xs font-medium text-text-tertiary underline-offset-4 hover:text-text-primary hover:underline"
                  >
                    Clear all
                  </button>
                </>
              )}

              {/* Sort móvil */}
              <Select
                size="sm"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                fullWidth={false}
                aria-label="Sort"
                className="sm:hidden ml-auto w-36"
                containerClassName="sm:hidden ml-auto"
              >
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="salary">Salary</option>
                <option value="relevance">Relevance</option>
              </Select>
            </div>
          )}
        </div>
      </div>

      {/* Listado */}
      <div className="mx-auto max-w-4xl px-4 py-5 sm:px-6 sm:py-6">
        {isLoading && (
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        )}

        {isError && (
          <div className="rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
            <p className="font-medium">Error loading jobs</p>
            <p className="mt-0.5 text-error/80">{error?.message}</p>
          </div>
        )}

        {!isLoading && !isError && jobs.length === 0 && (
          <EmptyState
            icon={Briefcase}
            title="No jobs match your search"
            description="Try removing some filters or broadening your query to see more results."
            action={
              hasActiveFilters && (
                <Button variant="primary" onClick={resetFilters}>
                  Clear filters
                </Button>
              )
            }
          />
        )}

        {!isLoading && !isError && jobs.length > 0 && (
          <div className="space-y-3">
            {jobs.map((job) => (
              <JobCard key={job.hash} job={job} />
            ))}
          </div>
        )}

        <div ref={sentinelRef} className="h-6" />

        {isFetchingNextPage && (
          <div className="flex justify-center py-6">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-ink" />
          </div>
        )}
      </div>

      <FilterPanel />
    </div>
  );
}

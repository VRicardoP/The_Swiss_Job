import { useCallback, useEffect, useMemo, useRef } from "react";
import { useSearchStore } from "../stores/searchStore";
import { useJobSearch } from "../hooks/useJobSearch";
import JobCard from "../components/JobCard";
import FilterPanel from "../components/FilterPanel";

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

export default function SearchPage() {
  const { q, sort, setQ, setSort, setFiltersOpen } = useSearchStore();
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

  // Infinite scroll observer
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

  return (
    <div className="min-h-screen">
      {/* Sticky header */}
      <header className="sticky top-16 z-30 bg-surface/80 backdrop-blur-lg border-b border-border shadow-xs">
        <div className="max-w-3xl mx-auto px-4 py-3">
          <div className="flex gap-2">
            <div className="relative flex-1">
              <svg
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-tertiary"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              <input
                type="text"
                defaultValue={q}
                onChange={(e) => debouncedSetQ(e.target.value)}
                placeholder="Search jobs..."
                className="w-full border border-border rounded-xl bg-surface-secondary pl-10 pr-4 py-2.5 text-sm placeholder:text-text-tertiary focus:outline-none focus:border-swiss-red focus:ring-2 focus:ring-swiss-red/20 transition-all"
              />
            </div>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="rounded-xl border border-border bg-surface-secondary px-2 py-2.5 text-sm"
            >
              <option value="newest">Newest</option>
              <option value="oldest">Oldest</option>
              <option value="salary">Salary</option>
              <option value="relevance">Relevance</option>
            </select>
            <button
              onClick={() => setFiltersOpen(true)}
              className="rounded-xl border border-border px-3 py-2.5 text-sm hover:bg-swiss-red-light hover:text-swiss-red hover:border-swiss-red/20 transition-all duration-200"
              aria-label="Open filters"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"
                />
              </svg>
            </button>
          </div>
          {!isLoading && (
            <p className="mt-1.5 text-sm text-text-secondary font-medium">
              {total} job{total !== 1 ? "s" : ""} found
            </p>
          )}
        </div>
      </header>

      {/* Content */}
      <main className="max-w-3xl mx-auto px-4 py-4 space-y-3">
        {isLoading && (
          <div className="flex justify-center py-12">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
          </div>
        )}

        {isError && (
          <div className="bg-error-light text-error rounded-xl p-4 text-sm">
            Error loading jobs: {error.message}
          </div>
        )}

        {!isLoading && !isError && jobs.length === 0 && (
          <div className="text-center py-12">
            <p className="text-text-tertiary">No jobs found. Try adjusting your filters.</p>
          </div>
        )}

        {jobs.map((job) => (
          <JobCard key={job.hash} job={job} />
        ))}

        {/* Infinite scroll sentinel */}
        <div ref={sentinelRef} className="h-4" />

        {isFetchingNextPage && (
          <div className="flex justify-center py-4">
            <div className="h-6 w-6 animate-spin rounded-full border-3 border-border border-t-swiss-red" />
          </div>
        )}
      </main>

      <FilterPanel />
    </div>
  );
}

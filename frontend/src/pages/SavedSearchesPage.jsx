import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useSavedSearches,
  useCreateSearch,
  useUpdateSearch,
  useDeleteSearch,
  useRunSearch,
} from "../hooks/useSavedSearches";
import { useSearchStore } from "../stores/searchStore";

const FREQ_OPTIONS = ["realtime", "daily", "weekly"];

function SearchCard({ search, onToggle, onRun, onDelete }) {
  const run = useRunSearch();
  const [running, setRunning] = useState(false);

  async function handleRun() {
    setRunning(true);
    try {
      await run.mutateAsync(search.id);
      onRun?.();
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="bg-surface shadow-card rounded-xl hover:shadow-card-hover transition-all p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-medium text-text-primary">{search.name}</h3>
          <p className="mt-1 text-xs text-text-secondary">
            {Object.entries(search.filters || {})
              .filter(([, v]) => v)
              .map(([k, v]) => `${k}: ${v}`)
              .join(", ") || "No filters"}
          </p>
          <div className="mt-1 flex items-center gap-2 text-xs text-text-tertiary">
            <span>Min score: {search.min_score}</span>
            <span>Freq: {search.notify_frequency}</span>
            <span>Matches: {search.total_matches}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onToggle(search.id, !search.is_active)}
            className={`rounded-full px-2 py-1 text-xs font-medium ${
              search.is_active
                ? "bg-success-light text-success"
                : "bg-surface-tertiary text-text-tertiary"
            }`}
          >
            {search.is_active ? "Active" : "Paused"}
          </button>
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          onClick={handleRun}
          disabled={running}
          className="rounded-full bg-swiss-red px-3 py-1 text-xs text-white hover:bg-swiss-red-hover disabled:opacity-50"
        >
          {running ? "Running..." : "Run now"}
        </button>
        <button
          onClick={() => onDelete(search.id)}
          className="rounded-full bg-error-light px-3 py-1 text-xs text-error hover:text-error/80"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export default function SavedSearchesPage() {
  const { data, isLoading, error } = useSavedSearches();
  const createSearch = useCreateSearch();
  const updateSearch = useUpdateSearch();
  const deleteSearch = useDeleteSearch();

  const searchStore = useSearchStore();

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [minScore, setMinScore] = useState(50);
  const [frequency, setFrequency] = useState("daily");

  function handleCreate(e) {
    e.preventDefault();
    if (!name.trim()) return;

    const filters = {};
    if (searchStore.q) filters.q = searchStore.q;
    if (searchStore.source) filters.source = searchStore.source;
    if (searchStore.canton) filters.canton = searchStore.canton;
    if (searchStore.language) filters.language = searchStore.language;
    if (searchStore.remoteOnly) filters.remote_only = true;

    createSearch.mutate(
      {
        name: name.trim(),
        filters,
        min_score: minScore,
        notify_frequency: frequency,
        notify_push: true,
      },
      {
        onSuccess: () => {
          setName("");
          setShowForm(false);
        },
      }
    );
  }

  function handleToggle(id, active) {
    updateSearch.mutate({ id, data: { is_active: active } });
  }

  function handleDelete(id) {
    deleteSearch.mutate(id);
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <p className="text-error">Error: {error.message}</p>
      </div>
    );
  }

  const searches = data?.data || [];

  return (
    <div className="mx-auto max-w-3xl p-4">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-primary tracking-tight">Saved Searches</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded-full bg-swiss-red px-5 py-1.5 text-sm font-semibold text-white hover:bg-swiss-red-hover shadow-xs transition-all duration-200"
        >
          {showForm ? "Cancel" : "New Search"}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleCreate}
          className="mb-4 bg-surface shadow-card rounded-xl border border-border p-4"
        >
          <p className="mb-2 text-xs text-text-secondary">
            Saves current search filters from the home page.
          </p>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Search name..."
            className="mb-2 w-full rounded-xl border border-border bg-surface-secondary px-3 py-2 text-sm focus:border-swiss-red focus:outline-none"
          />
          <div className="mb-2 flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
                Min Score
              </label>
              <input
                type="number"
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                min={0}
                max={100}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-1.5 text-sm focus:border-swiss-red focus:outline-none"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-text-tertiary">
                Alert Frequency
              </label>
              <select
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
                className="w-full rounded-xl border border-border bg-surface-secondary px-3 py-1.5 text-sm focus:border-swiss-red focus:outline-none"
              >
                {FREQ_OPTIONS.map((f) => (
                  <option key={f} value={f}>
                    {f.charAt(0).toUpperCase() + f.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <button
            type="submit"
            disabled={createSearch.isPending || !name.trim()}
            className="w-full rounded-xl bg-swiss-red py-2 text-sm font-semibold text-white hover:bg-swiss-red-hover disabled:opacity-50"
          >
            {createSearch.isPending ? "Saving..." : "Save Search"}
          </button>
        </form>
      )}

      {searches.length === 0 ? (
        <div className="mt-20 text-center">
          <p className="text-lg text-text-tertiary">No saved searches</p>
          <p className="mt-1 text-sm text-text-tertiary">
            Set filters on the{" "}
            <Link to="/" className="text-swiss-red hover:underline">
              Search
            </Link>{" "}
            page, then save them here for alerts.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {searches.map((s) => (
            <SearchCard
              key={s.id}
              search={s}
              onToggle={handleToggle}
              onRun={() => {}}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}

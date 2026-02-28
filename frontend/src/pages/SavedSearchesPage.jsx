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
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-medium text-gray-900">{search.name}</h3>
          <p className="mt-1 text-xs text-gray-500">
            {Object.entries(search.filters || {})
              .filter(([, v]) => v)
              .map(([k, v]) => `${k}: ${v}`)
              .join(", ") || "No filters"}
          </p>
          <div className="mt-1 flex items-center gap-2 text-xs text-gray-400">
            <span>Min score: {search.min_score}</span>
            <span>Freq: {search.notify_frequency}</span>
            <span>Matches: {search.total_matches}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onToggle(search.id, !search.is_active)}
            className={`rounded px-2 py-1 text-xs font-medium ${
              search.is_active
                ? "bg-green-100 text-green-700"
                : "bg-gray-100 text-gray-500"
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
          className="rounded bg-blue-500 px-3 py-1 text-xs text-white hover:bg-blue-600 disabled:opacity-50"
        >
          {running ? "Running..." : "Run now"}
        </button>
        <button
          onClick={() => onDelete(search.id)}
          className="rounded bg-red-50 px-3 py-1 text-xs text-red-600 hover:bg-red-100"
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
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <p className="text-red-600">Error: {error.message}</p>
      </div>
    );
  }

  const searches = data?.data || [];

  return (
    <div className="mx-auto max-w-2xl p-4">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Saved Searches</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
        >
          {showForm ? "Cancel" : "New Search"}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleCreate}
          className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-4"
        >
          <p className="mb-2 text-xs text-gray-500">
            Saves current search filters from the home page.
          </p>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Search name..."
            className="mb-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
          <div className="mb-2 flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs text-gray-600">
                Min Score
              </label>
              <input
                type="number"
                value={minScore}
                onChange={(e) => setMinScore(Number(e.target.value))}
                min={0}
                max={100}
                className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
              />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-xs text-gray-600">
                Alert Frequency
              </label>
              <select
                value={frequency}
                onChange={(e) => setFrequency(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
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
            className="w-full rounded-lg bg-blue-600 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {createSearch.isPending ? "Saving..." : "Save Search"}
          </button>
        </form>
      )}

      {searches.length === 0 ? (
        <div className="mt-20 text-center">
          <p className="text-lg text-gray-500">No saved searches</p>
          <p className="mt-1 text-sm text-gray-400">
            Set filters on the{" "}
            <Link to="/" className="text-blue-600 hover:underline">
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

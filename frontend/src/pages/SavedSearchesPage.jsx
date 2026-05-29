import { useState } from "react";
import {
  Bell,
  Play,
  Plus,
  Trash2,
  X,
  PauseCircle,
  PlayCircle,
} from "lucide-react";
import {
  useSavedSearches,
  useCreateSearch,
  useUpdateSearch,
  useDeleteSearch,
  useRunSearch,
} from "../hooks/useSavedSearches";
import { useSearchStore } from "../stores/searchStore";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  LinkButton,
  PageHeader,
  Select,
  SkeletonCard,
  cn,
} from "../components/ui";

const FREQ_OPTIONS = [
  { value: "realtime", label: "Realtime" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

function SearchCard({ search, onToggle, onDelete }) {
  const run = useRunSearch();
  const [running, setRunning] = useState(false);

  async function handleRun() {
    setRunning(true);
    try {
      await run.mutateAsync(search.id);
    } finally {
      setRunning(false);
    }
  }

  const activeFilters = Object.entries(search.filters || {}).filter(
    ([, v]) => v,
  );

  return (
    <Card padding="md" className="transition-colors hover:border-border-strong">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-semibold tracking-tight text-text-primary">
              {search.name}
            </h3>
            <Badge
              variant={search.is_active ? "success" : "neutral"}
              size="xs"
            >
              {search.is_active ? "Active" : "Paused"}
            </Badge>
          </div>

          {activeFilters.length > 0 ? (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {activeFilters.map(([k, v]) => (
                <Badge key={k} variant="outline" size="xs">
                  {k}: {String(v)}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="mt-1.5 text-xs italic text-text-tertiary">
              No filters
            </p>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-tertiary">
            <span>
              Min score{" "}
              <span className="font-medium text-text-primary tabular-nums">
                {search.min_score}
              </span>
            </span>
            <span aria-hidden="true">·</span>
            <span className="capitalize">{search.notify_frequency}</span>
            <span aria-hidden="true">·</span>
            <span>
              {search.total_matches}{" "}
              {search.total_matches === 1 ? "match" : "matches"}
            </span>
          </div>
        </div>

        <IconButton
          aria-label={search.is_active ? "Pause" : "Resume"}
          variant="ghost"
          onClick={() => onToggle(search.id, !search.is_active)}
          title={search.is_active ? "Pause alerts" : "Resume alerts"}
        >
          {search.is_active ? (
            <PauseCircle className="h-4 w-4" />
          ) : (
            <PlayCircle className="h-4 w-4" />
          )}
        </IconButton>
      </div>

      <div className="mt-3 flex items-center gap-2 border-t border-border-light pt-3">
        <Button
          variant="secondary"
          size="sm"
          leftIcon={<Play className="h-3.5 w-3.5" />}
          loading={running}
          onClick={handleRun}
        >
          Run now
        </Button>
        <IconButton
          aria-label="Delete saved search"
          variant="ghost"
          size="sm"
          onClick={() => onDelete(search.id)}
          className="ml-auto text-text-tertiary hover:bg-error-light hover:text-error"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </IconButton>
      </div>
    </Card>
  );
}

function NewSearchForm({ onCancel, onSubmit, pending, searchStore }) {
  const [name, setName] = useState("");
  const [minScore, setMinScore] = useState(50);
  const [frequency, setFrequency] = useState("daily");

  function handleSubmit(e) {
    e.preventDefault();
    if (!name.trim()) return;
    const filters = {};
    if (searchStore.q) filters.q = searchStore.q;
    if (searchStore.source) filters.source = searchStore.source;
    if (searchStore.canton) filters.canton = searchStore.canton;
    if (searchStore.language) filters.language = searchStore.language;
    if (searchStore.remoteOnly) filters.remote_only = true;

    onSubmit({
      name: name.trim(),
      filters,
      min_score: minScore,
      notify_frequency: frequency,
      notify_push: true,
    });
  }

  return (
    <Card padding="lg" className="animate-fade-in-up">
      <form onSubmit={handleSubmit} className="space-y-4">
        <header className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold tracking-tight text-text-primary">
              New saved search
            </h3>
            <p className="mt-0.5 text-xs text-text-secondary">
              Captures the current filters from the Search page.
            </p>
          </div>
          <IconButton
            aria-label="Cancel"
            variant="ghost"
            size="sm"
            onClick={onCancel}
          >
            <X className="h-4 w-4" />
          </IconButton>
        </header>

        <Input
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Remote senior backend roles"
          required
        />

        <div className="grid grid-cols-2 gap-3">
          <Input
            label="Min score"
            type="number"
            inputMode="numeric"
            min={0}
            max={100}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
          />
          <Select
            label="Alert frequency"
            value={frequency}
            onChange={(e) => setFrequency(e.target.value)}
          >
            {FREQ_OPTIONS.map((f) => (
              <option key={f.value} value={f.value}>
                {f.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={pending}
            disabled={!name.trim()}
            leftIcon={<Bell className="h-4 w-4" />}
          >
            Save & enable
          </Button>
        </div>
      </form>
    </Card>
  );
}

export default function SavedSearchesPage() {
  const { data, isLoading, error } = useSavedSearches();
  const createSearch = useCreateSearch();
  const updateSearch = useUpdateSearch();
  const deleteSearch = useDeleteSearch();
  const searchStore = useSearchStore();
  const [showForm, setShowForm] = useState(false);

  function handleToggle(id, active) {
    updateSearch.mutate({ id, data: { is_active: active } });
  }
  function handleDelete(id) {
    deleteSearch.mutate(id);
  }
  function handleCreate(payload) {
    createSearch.mutate(payload, {
      onSuccess: () => setShowForm(false),
    });
  }

  const searches = data?.data || [];
  const activeCount = searches.filter((s) => s.is_active).length;

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="Alerts"
        title="Saved searches"
        description={
          searches.length > 0
            ? `${activeCount} active · ${searches.length} total`
            : "Re-run any search on a schedule and get alerts for new matches."
        }
        actions={
          !showForm && (
            <Button
              variant="primary"
              leftIcon={<Plus className="h-4 w-4" />}
              onClick={() => setShowForm(true)}
            >
              New search
            </Button>
          )
        }
      />

      <div className="mt-6 space-y-4">
        {showForm && (
          <NewSearchForm
            onCancel={() => setShowForm(false)}
            onSubmit={handleCreate}
            pending={createSearch.isPending}
            searchStore={searchStore}
          />
        )}

        {error && (
          <div className="rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
            {error.message}
          </div>
        )}

        {isLoading ? (
          <div className="space-y-3">
            <SkeletonCard />
            <SkeletonCard />
          </div>
        ) : searches.length === 0 && !showForm ? (
          <EmptyState
            icon={Bell}
            title="No saved searches yet"
            description="Set filters on the Search page, then save them here to get matches on a schedule."
            action={
              <LinkButton to="/" variant="primary">
                Go to Search
              </LinkButton>
            }
            secondaryAction={
              <Button
                variant="secondary"
                leftIcon={<Plus className="h-4 w-4" />}
                onClick={() => setShowForm(true)}
              >
                New search
              </Button>
            }
          />
        ) : (
          <div className={cn("space-y-3")}>
            {searches.map((s) => (
              <SearchCard
                key={s.id}
                search={s}
                onToggle={handleToggle}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

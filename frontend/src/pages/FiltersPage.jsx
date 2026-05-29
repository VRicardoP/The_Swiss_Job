import { useState } from "react";
import {
  Sparkles,
  Plus,
  X,
  Check,
  Trash2,
  Filter as FilterIcon,
  Wand2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import {
  useAnalyzeSuggestions,
  useSuggestions,
  useReviewSuggestion,
  useFilters,
  useDeleteFilter,
  useCreateFilter,
} from "../hooks/useAnalytics";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  PageHeader,
  Select,
  SkeletonCard,
  cn,
} from "../components/ui";

function confidenceTone(c) {
  if (c >= 0.75) return { label: "High", variant: "error" };
  if (c >= 0.5) return { label: "Medium", variant: "warning" };
  return { label: "Low", variant: "neutral" };
}

function SuggestionCard({ suggestion, onApprove, onReject, loading }) {
  const [expanded, setExpanded] = useState(false);
  const pct = Math.round(suggestion.confidence * 100);
  const tone = confidenceTone(suggestion.confidence);

  return (
    <Card padding="md" className="space-y-3">
      <header className="flex flex-wrap items-center gap-2">
        <Badge variant={tone.variant} size="sm">
          {tone.label} · {pct}%
        </Badge>
        <Badge variant="neutral" size="xs">
          {suggestion.suggestion_type === "title_pattern"
            ? "Title pattern"
            : "Tag / category"}
        </Badge>
        <span className="text-xs text-text-tertiary">
          {suggestion.affected_count} rejected
          {suggestion.affected_count !== 1 ? "s" : ""}
        </span>
      </header>

      <div>
        <code className="inline-block rounded-md bg-surface-tertiary px-2 py-1 font-mono text-sm font-medium text-text-primary">
          “{suggestion.pattern}”
        </code>
        <p className="mt-2 text-sm text-text-secondary">
          {suggestion.description}
        </p>
      </div>

      {suggestion.sample_jobs?.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex items-center gap-1 text-xs font-medium text-text-secondary hover:text-text-primary"
          >
            {expanded ? (
              <>
                <ChevronUp className="h-3.5 w-3.5" />
                Hide examples
              </>
            ) : (
              <>
                <ChevronDown className="h-3.5 w-3.5" />
                Show {suggestion.sample_jobs.length} example
                {suggestion.sample_jobs.length !== 1 ? "s" : ""}
              </>
            )}
          </button>
          {expanded && (
            <ul className="mt-2 space-y-1">
              {suggestion.sample_jobs.map((j, i) => (
                <li
                  key={i}
                  className="rounded-md bg-surface-secondary px-3 py-1.5 text-xs"
                >
                  <span className="font-medium text-text-primary">
                    {j.title}
                  </span>
                  {j.company && (
                    <span className="text-text-tertiary"> — {j.company}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 border-t border-border-light pt-3">
        <Button
          variant="primary"
          size="sm"
          leftIcon={<Check className="h-3.5 w-3.5" />}
          disabled={loading}
          fullWidth
          onClick={() => onApprove(suggestion.id)}
        >
          Enable filter
        </Button>
        <Button
          variant="ghost"
          size="sm"
          leftIcon={<X className="h-3.5 w-3.5" />}
          disabled={loading}
          onClick={() => onReject(suggestion.id)}
        >
          Dismiss
        </Button>
      </div>
    </Card>
  );
}

function FilterRow({ filter, onDelete }) {
  return (
    <Card padding="sm" className="flex items-center gap-3">
      <Badge variant="ink" size="xs">
        {filter.filter_type === "title_contains" ? "title" : "tag"}
      </Badge>
      <code className="min-w-0 flex-1 truncate font-mono text-sm font-medium text-text-primary">
        “{filter.pattern}”
      </code>
      {filter.hit_count > 0 && (
        <span className="shrink-0 text-xs text-text-tertiary tabular-nums">
          {filter.hit_count} hit{filter.hit_count !== 1 ? "s" : ""}
        </span>
      )}
      <Badge variant={filter.source === "manual" ? "outline" : "info"} size="xs">
        {filter.source}
      </Badge>
      <IconButton
        aria-label="Delete filter"
        variant="ghost"
        size="sm"
        onClick={() => onDelete(filter.id)}
        className="text-text-tertiary hover:bg-error-light hover:text-error"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </IconButton>
    </Card>
  );
}

function AddFilterForm({ onAdd }) {
  const [type, setType] = useState("title_contains");
  const [pattern, setPattern] = useState("");
  const [description, setDescription] = useState("");
  const [open, setOpen] = useState(false);

  function handleSubmit(e) {
    e.preventDefault();
    if (!pattern.trim()) return;
    onAdd({
      filter_type: type,
      pattern: pattern.trim().toLowerCase(),
      description: description.trim() || null,
    });
    setPattern("");
    setDescription("");
    setOpen(false);
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-surface",
          "py-3 text-sm font-medium text-text-secondary",
          "transition-colors hover:border-ink hover:text-text-primary",
        )}
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
        Add manual filter
      </button>
    );
  }

  return (
    <Card padding="lg" className="animate-fade-in-up">
      <form onSubmit={handleSubmit} className="space-y-3">
        <header className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold tracking-tight text-text-primary">
            New manual filter
          </h3>
          <IconButton
            aria-label="Cancel"
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
          >
            <X className="h-4 w-4" />
          </IconButton>
        </header>

        <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
          <Select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="title_contains">Title contains</option>
            <option value="tag_contains">Tag contains</option>
          </Select>
          <Input
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            placeholder='e.g. "developer", "java"'
            required
          />
        </div>

        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Optional description"
        />

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" leftIcon={<Plus className="h-4 w-4" />}>
            Create filter
          </Button>
        </div>
      </form>
    </Card>
  );
}

export default function FiltersPage() {
  const [tab, setTab] = useState("suggestions");

  const { mutate: analyze, isPending: analyzing } = useAnalyzeSuggestions();
  const { data: suggestionsData, isLoading: loadingSuggestions } =
    useSuggestions("pending");
  const { mutate: review, isPending: reviewing } = useReviewSuggestion();
  const { data: filtersData, isLoading: loadingFilters } = useFilters();
  const { mutate: deleteFilter } = useDeleteFilter();
  const { mutate: createFilter } = useCreateFilter();

  const suggestions = suggestionsData?.data ?? [];
  const filters = filtersData?.data ?? [];

  const handleApprove = (id) => review({ id, action: "approve" });
  const handleReject = (id) => review({ id, action: "reject" });

  const TABS = [
    {
      key: "suggestions",
      label: "Suggestions",
      count: suggestions.length,
    },
    {
      key: "filters",
      label: "Active filters",
      count: filters.length,
    },
  ];

  return (
    <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="Matching"
        title="Exclusion filters"
        description="Analyse your rejected jobs to detect patterns and create filters that improve match quality."
        actions={
          tab === "suggestions" && (
            <Button
              variant="primary"
              leftIcon={<Wand2 className="h-4 w-4" />}
              loading={analyzing}
              onClick={() => analyze({ min_rejected: 2 })}
            >
              Analyse rejected
            </Button>
          )
        }
      />

      {/* Tabs segmented */}
      <div className="mt-6 inline-flex rounded-lg border border-border bg-surface-secondary p-0.5">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            aria-pressed={tab === t.key}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm font-medium transition-all",
              tab === t.key
                ? "bg-surface text-text-primary shadow-xs"
                : "text-text-tertiary hover:text-text-primary",
            )}
          >
            {t.label}
            {t.count > 0 && (
              <Badge
                variant={tab === t.key ? "ink" : "neutral"}
                size="xs"
              >
                {t.count}
              </Badge>
            )}
          </button>
        ))}
      </div>

      {/* Panel sugerencias */}
      {tab === "suggestions" && (
        <section className="mt-5 space-y-4">
          {loadingSuggestions ? (
            <div className="space-y-3">
              <SkeletonCard />
              <SkeletonCard />
            </div>
          ) : suggestions.length === 0 ? (
            <EmptyState
              icon={Sparkles}
              title="No suggestions yet"
              description="Reject a few jobs from Matches, then click Analyse to find recurring patterns."
              action={
                <Button
                  variant="primary"
                  leftIcon={<Wand2 className="h-4 w-4" />}
                  loading={analyzing}
                  onClick={() => analyze({ min_rejected: 2 })}
                >
                  Analyse rejected
                </Button>
              }
            />
          ) : (
            <div className="space-y-3">
              {suggestions.map((s) => (
                <SuggestionCard
                  key={s.id}
                  suggestion={s}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  loading={reviewing}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {/* Panel filtros activos */}
      {tab === "filters" && (
        <section className="mt-5 space-y-3">
          {loadingFilters ? (
            <>
              <SkeletonCard />
              <SkeletonCard />
            </>
          ) : filters.length === 0 ? (
            <EmptyState
              icon={FilterIcon}
              title="No active filters"
              description="Approve suggestions or add a manual filter to exclude categories from your matching."
            />
          ) : (
            <div className="space-y-2">
              {filters.map((f) => (
                <FilterRow
                  key={f.id}
                  filter={f}
                  onDelete={(id) => deleteFilter(id)}
                />
              ))}
            </div>
          )}

          <AddFilterForm onAdd={(data) => createFilter(data)} />

          <p className="text-center text-xs text-text-tertiary">
            Active filters are applied to the next match analysis.
          </p>
        </section>
      )}
    </div>
  );
}

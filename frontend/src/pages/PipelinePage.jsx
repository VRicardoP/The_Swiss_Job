import { memo, useCallback } from "react";
import {
  ArrowRight,
  X,
  Trash2,
  FileText,
  Inbox,
  TrendingUp,
} from "lucide-react";
import {
  useApplications,
  useApplicationStats,
  useUpdateApplication,
  useDeleteApplication,
} from "../hooks/useApplications";
import {
  Avatar,
  Badge,
  EmptyState,
  IconButton,
  LinkButton,
  MetricTile,
  PageHeader,
  Skeleton,
  cn,
} from "../components/ui";

// Cada columna define el estado, label y acento superior del kanban.
const COLUMNS = [
  { key: "saved",        label: "Saved",        tone: "bg-ink-300" },
  { key: "applied",      label: "Applied",      tone: "bg-info" },
  { key: "phone_screen", label: "Phone screen", tone: "bg-warning" },
  { key: "technical",    label: "Technical",    tone: "bg-warning" },
  { key: "interview",    label: "Interview",    tone: "bg-swiss-red" },
  { key: "offer",        label: "Offer",        tone: "bg-success" },
  { key: "rejected",     label: "Rejected",     tone: "bg-error" },
  { key: "withdrawn",    label: "Withdrawn",    tone: "bg-ink-300" },
];

const NEXT_STATUS = {
  saved: "applied",
  applied: "phone_screen",
  phone_screen: "technical",
  technical: "interview",
  interview: "offer",
};

const ApplicationCard = memo(function ApplicationCard({
  app,
  onAdvance,
  onReject,
  onDelete,
}) {
  const next = NEXT_STATUS[app.status];
  const canReject = app.status !== "rejected" && app.status !== "withdrawn";

  return (
    <article className="rounded-lg border border-border bg-surface p-3 transition-all duration-150 hover:border-border-strong hover:shadow-card">
      <div className="flex items-start gap-2.5">
        <Avatar name={app.job_company || "?"} size="sm" />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-1 text-sm font-medium tracking-tight text-text-primary">
            {app.job_title || "Untitled"}
          </p>
          <p className="line-clamp-1 text-xs text-text-secondary">
            {app.job_company || "Unknown"}
            {app.job_location && ` · ${app.job_location}`}
          </p>
        </div>
      </div>

      {app.notes && (
        <p className="mt-2 line-clamp-2 rounded-md bg-surface-secondary px-2 py-1.5 text-xs italic text-text-secondary">
          {app.notes}
        </p>
      )}

      <div className="mt-3 flex items-center gap-1.5">
        {next && (
          <button
            type="button"
            onClick={() => onAdvance(app.id, next)}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-md border border-border bg-surface px-2 text-[11px] font-medium text-text-primary",
              "hover:border-ink hover:bg-ink hover:text-text-inverse transition-colors",
            )}
            title={`Move to ${next.replace("_", " ")}`}
          >
            <span className="capitalize">{next.replace("_", " ")}</span>
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </button>
        )}

        {canReject && (
          <IconButton
            aria-label="Reject"
            variant="ghost"
            size="sm"
            onClick={() => onReject(app.id)}
            className="hover:bg-error-light hover:text-error"
          >
            <X className="h-3.5 w-3.5" />
          </IconButton>
        )}

        <LinkButton
          to={`/job/${app.job_hash}`}
          variant="ghost"
          size="xs"
          leftIcon={<FileText className="h-3 w-3" />}
          className="text-text-secondary"
        >
          Docs
        </LinkButton>

        <IconButton
          aria-label="Remove"
          variant="ghost"
          size="sm"
          onClick={() => onDelete(app.id)}
          className="ml-auto text-text-tertiary hover:bg-error-light hover:text-error"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </IconButton>
      </div>
    </article>
  );
});

function ColumnSkeleton() {
  return (
    <div className="w-[260px] shrink-0 space-y-2">
      <Skeleton className="h-9 w-full rounded-lg" />
      <Skeleton className="h-24 w-full rounded-lg" />
      <Skeleton className="h-24 w-full rounded-lg" />
    </div>
  );
}

export default function PipelinePage() {
  const { data, isLoading, error } = useApplications({ limit: 200 });
  const { data: stats } = useApplicationStats();
  const updateApp = useUpdateApplication();
  const deleteApp = useDeleteApplication();

  const handleAdvance = useCallback(
    (id, newStatus) => updateApp.mutate({ id, data: { status: newStatus } }),
    [updateApp],
  );
  const handleReject = useCallback(
    (id) => updateApp.mutate({ id, data: { status: "rejected" } }),
    [updateApp],
  );
  const handleDelete = useCallback((id) => deleteApp.mutate(id), [deleteApp]);

  const applications = data?.data || [];
  const conversion = stats?.conversion_rates?.saved_to_applied;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <PageHeader
        eyebrow="Career"
        title="Pipeline"
        description="Track every application from saved to offer."
        actions={
          <LinkButton
            to="/match"
            variant="primary"
            leftIcon={<TrendingUp className="h-4 w-4" />}
          >
            Find matches
          </LinkButton>
        }
      />

      {/* Métricas */}
      {stats && (
        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {["saved", "applied", "interview", "offer"].map((key) => (
            <MetricTile
              key={key}
              label={key.replace("_", " ")}
              value={stats.by_status?.[key] ?? 0}
              tone={
                key === "offer" ? "success" : key === "interview" ? "brand" : "neutral"
              }
            />
          ))}
          {conversion !== undefined && (
            <MetricTile
              label="Apply rate"
              value={`${(conversion * 100).toFixed(0)}%`}
              tone="ink"
              hint="Saved → Applied"
            />
          )}
          <MetricTile
            label="Active"
            value={applications.filter(
              (a) => a.status !== "rejected" && a.status !== "withdrawn",
            ).length}
            tone="ink"
          />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mt-6 rounded-xl border border-error-border bg-error-light p-4 text-sm text-error">
          {error.message}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="mt-6 flex gap-3 overflow-x-auto pb-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <ColumnSkeleton key={i} />
          ))}
        </div>
      )}

      {/* Empty */}
      {!isLoading && applications.length === 0 && (
        <div className="mt-6">
          <EmptyState
            icon={Inbox}
            title="No applications yet"
            description="Save jobs from the Matches page and they'll show up here as you progress."
            action={
              <LinkButton to="/match" variant="primary">
                Go to Matches
              </LinkButton>
            }
          />
        </div>
      )}

      {/* Kanban */}
      {!isLoading && applications.length > 0 && (
        <div className="mt-6 flex gap-3 overflow-x-auto pb-4 snap-x snap-mandatory">
          {COLUMNS.map((col) => {
            const items = applications.filter((a) => a.status === col.key);
            // Ocultar columnas terminales vacías
            if (
              items.length === 0 &&
              !["saved", "applied", "interview", "offer"].includes(col.key)
            ) {
              return null;
            }
            return (
              <div
                key={col.key}
                className="w-[260px] shrink-0 snap-start rounded-xl bg-surface-secondary"
              >
                {/* Cabecera columna */}
                <header
                  className={cn(
                    "sticky top-0 z-10 flex items-center justify-between rounded-t-xl bg-surface-secondary/95 px-3 py-2.5 backdrop-blur",
                    "border-b border-border-light",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className={cn("h-1.5 w-1.5 rounded-full", col.tone)}
                      aria-hidden="true"
                    />
                    <h3 className="text-sm font-medium tracking-tight text-text-primary">
                      {col.label}
                    </h3>
                  </div>
                  <Badge variant="neutral" size="xs">
                    {items.length}
                  </Badge>
                </header>

                {/* Cards */}
                <div className="flex flex-col gap-2 p-2">
                  {items.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-border bg-surface px-3 py-4 text-center text-xs text-text-tertiary">
                      Empty
                    </p>
                  ) : (
                    items.map((app) => (
                      <ApplicationCard
                        key={app.id}
                        app={app}
                        onAdvance={handleAdvance}
                        onReject={handleReject}
                        onDelete={handleDelete}
                      />
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

    </div>
  );
}

import { memo, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  useApplications,
  useApplicationStats,
  useUpdateApplication,
  useDeleteApplication,
} from "../hooks/useApplications";

const COLUMNS = [
  { key: "saved", label: "Saved", border: "border-t-text-tertiary" },
  { key: "applied", label: "Applied", border: "border-t-info" },
  { key: "phone_screen", label: "Phone Screen", border: "border-t-warning" },
  { key: "technical", label: "Technical", border: "border-t-warning" },
  { key: "interview", label: "Interview", border: "border-t-swiss-red" },
  { key: "offer", label: "Offer", border: "border-t-success" },
  { key: "rejected", label: "Rejected", border: "border-t-error" },
  { key: "withdrawn", label: "Withdrawn", border: "border-t-text-tertiary" },
];

const NEXT_STATUS = {
  saved: "applied",
  applied: "phone_screen",
  phone_screen: "technical",
  technical: "interview",
  interview: "offer",
};

function StatusBadge({ status }) {
  const colors = {
    saved: "bg-surface-tertiary text-text-secondary",
    applied: "bg-info-light text-info",
    phone_screen: "bg-warning-light text-warning",
    technical: "bg-warning-light text-warning",
    interview: "bg-swiss-red-light text-swiss-red",
    offer: "bg-success-light text-success",
    rejected: "bg-error-light text-error",
    withdrawn: "bg-surface-tertiary text-text-tertiary",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${colors[status] || ""}`}>
      {status.replace("_", " ")}
    </span>
  );
}

const ApplicationCard = memo(function ApplicationCard({ app, onAdvance, onReject, onDelete }) {
  const next = NEXT_STATUS[app.status];
  return (
    <div className="bg-surface rounded-xl shadow-xs hover:shadow-card p-4 transition-all duration-200">
      <p className="text-sm font-medium text-text-primary line-clamp-1">
        {app.job_title || "Untitled"}
      </p>
      <p className="text-xs text-text-secondary">{app.job_company || "Unknown"}</p>
      {app.job_location && (
        <p className="text-xs text-text-tertiary">{app.job_location}</p>
      )}
      {app.notes && (
        <p className="mt-1 text-xs text-text-secondary italic line-clamp-2">{app.notes}</p>
      )}
      <div className="mt-2 flex items-center gap-1">
        {next && (
          <button
            onClick={() => onAdvance(app.id, next)}
            className="rounded-full bg-swiss-red px-2 py-0.5 text-xs text-white hover:bg-swiss-red-hover"
          >
            {next.replace("_", " ")}
          </button>
        )}
        {app.status !== "rejected" && app.status !== "withdrawn" && (
          <button
            onClick={() => onReject(app.id)}
            className="rounded-full bg-error-light px-2 py-0.5 text-xs text-error hover:opacity-80"
          >
            Reject
          </button>
        )}
        <Link
          to={`/job/${app.job_hash}`}
          className="text-xs text-swiss-red hover:underline"
        >
          AI Docs
        </Link>
        <button
          onClick={() => onDelete(app.id)}
          className="ml-auto text-xs text-text-tertiary hover:text-error"
        >
          Remove
        </button>
      </div>
    </div>
  );
});

export default function PipelinePage() {
  const { data, isLoading, error } = useApplications({ limit: 200 });
  const { data: stats } = useApplicationStats();
  const updateApp = useUpdateApplication();
  const deleteApp = useDeleteApplication();

  const handleAdvance = useCallback(
    (id, newStatus) => {
      updateApp.mutate({ id, data: { status: newStatus } });
    },
    [updateApp],
  );

  const handleReject = useCallback(
    (id) => {
      updateApp.mutate({ id, data: { status: "rejected" } });
    },
    [updateApp],
  );

  const handleDelete = useCallback(
    (id) => {
      deleteApp.mutate(id);
    },
    [deleteApp],
  );

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-swiss-red" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-2xl p-6">
        <p className="text-error">Error: {error.message}</p>
      </div>
    );
  }

  const applications = data?.data || [];

  return (
    <div className="mx-auto max-w-7xl p-4">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-text-primary tracking-tight">Pipeline</h1>
        <Link to="/match" className="text-sm text-swiss-red font-medium hover:underline">
          Find matches
        </Link>
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="mb-4 flex flex-wrap gap-3">
          {Object.entries(stats.by_status).map(([status, count]) => (
            <div key={status} className="flex items-center gap-1">
              <StatusBadge status={status} />
              <span className="text-sm font-medium text-text-primary">{count}</span>
            </div>
          ))}
          {stats.conversion_rates?.saved_to_applied !== undefined && (
            <span className="text-xs text-text-secondary">
              Conversion: {(stats.conversion_rates.saved_to_applied * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      {applications.length === 0 ? (
        <div className="mt-20 text-center">
          <p className="text-lg text-text-tertiary">No applications yet</p>
          <p className="mt-1 text-sm text-text-tertiary">
            Save jobs from the{" "}
            <Link to="/match" className="text-swiss-red hover:underline">
              Matches
            </Link>{" "}
            page to start tracking.
          </p>
        </div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4">
          {COLUMNS.map((col) => {
            const items = applications.filter((a) => a.status === col.key);
            if (items.length === 0 && !["saved", "applied", "interview", "offer"].includes(col.key)) {
              return null;
            }
            return (
              <div
                key={col.key}
                className={`min-w-[220px] shrink-0 bg-surface-secondary rounded-xl border-t-4 ${col.border} p-2`}
              >
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-text-primary">
                    {col.label}
                  </h3>
                  <span className="bg-surface rounded-full shadow-xs px-2 py-0.5 text-xs text-text-secondary">
                    {items.length}
                  </span>
                </div>
                <div className="flex flex-col gap-2">
                  {items.map((app) => (
                    <ApplicationCard
                      key={app.id}
                      app={app}
                      onAdvance={handleAdvance}
                      onReject={handleReject}
                      onDelete={handleDelete}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

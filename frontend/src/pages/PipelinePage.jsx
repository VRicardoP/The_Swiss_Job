import { memo, useCallback } from "react";
import { Link } from "react-router-dom";
import {
  useApplications,
  useApplicationStats,
  useUpdateApplication,
  useDeleteApplication,
} from "../hooks/useApplications";

const COLUMNS = [
  { key: "saved", label: "Saved", color: "bg-gray-100" },
  { key: "applied", label: "Applied", color: "bg-blue-50" },
  { key: "phone_screen", label: "Phone Screen", color: "bg-yellow-50" },
  { key: "technical", label: "Technical", color: "bg-orange-50" },
  { key: "interview", label: "Interview", color: "bg-purple-50" },
  { key: "offer", label: "Offer", color: "bg-green-50" },
  { key: "rejected", label: "Rejected", color: "bg-red-50" },
  { key: "withdrawn", label: "Withdrawn", color: "bg-gray-50" },
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
    saved: "bg-gray-200 text-gray-700",
    applied: "bg-blue-200 text-blue-800",
    phone_screen: "bg-yellow-200 text-yellow-800",
    technical: "bg-orange-200 text-orange-800",
    interview: "bg-purple-200 text-purple-800",
    offer: "bg-green-200 text-green-800",
    rejected: "bg-red-200 text-red-800",
    withdrawn: "bg-gray-200 text-gray-600",
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
    <div className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
      <p className="text-sm font-medium text-gray-900 line-clamp-1">
        {app.job_title || "Untitled"}
      </p>
      <p className="text-xs text-gray-500">{app.job_company || "Unknown"}</p>
      {app.job_location && (
        <p className="text-xs text-gray-400">{app.job_location}</p>
      )}
      {app.notes && (
        <p className="mt-1 text-xs text-gray-500 italic line-clamp-2">{app.notes}</p>
      )}
      <div className="mt-2 flex items-center gap-1">
        {next && (
          <button
            onClick={() => onAdvance(app.id, next)}
            className="rounded bg-blue-500 px-2 py-0.5 text-xs text-white hover:bg-blue-600"
          >
            {next.replace("_", " ")}
          </button>
        )}
        {app.status !== "rejected" && app.status !== "withdrawn" && (
          <button
            onClick={() => onReject(app.id)}
            className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-600 hover:bg-red-200"
          >
            Reject
          </button>
        )}
        <Link
          to={`/job/${app.job_hash}`}
          className="text-xs text-green-600 hover:underline"
        >
          AI Docs
        </Link>
        <button
          onClick={() => onDelete(app.id)}
          className="ml-auto text-xs text-gray-400 hover:text-red-500"
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

  const applications = data?.data || [];

  return (
    <div className="mx-auto max-w-7xl p-4">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Pipeline</h1>
        <Link to="/match" className="text-sm text-blue-600 hover:underline">
          Find matches
        </Link>
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="mb-4 flex flex-wrap gap-3">
          {Object.entries(stats.by_status).map(([status, count]) => (
            <div key={status} className="flex items-center gap-1">
              <StatusBadge status={status} />
              <span className="text-sm font-medium text-gray-700">{count}</span>
            </div>
          ))}
          {stats.conversion_rates?.saved_to_applied !== undefined && (
            <span className="text-xs text-gray-400">
              Conversion: {(stats.conversion_rates.saved_to_applied * 100).toFixed(0)}%
            </span>
          )}
        </div>
      )}

      {applications.length === 0 ? (
        <div className="mt-20 text-center">
          <p className="text-lg text-gray-500">No applications yet</p>
          <p className="mt-1 text-sm text-gray-400">
            Save jobs from the{" "}
            <Link to="/match" className="text-blue-600 hover:underline">
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
                className={`min-w-[220px] shrink-0 rounded-lg ${col.color} p-2`}
              >
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-gray-700">
                    {col.label}
                  </h3>
                  <span className="rounded-full bg-white px-2 py-0.5 text-xs text-gray-500">
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

import { useEffect, useRef, useState } from "react";
import { Bell, Inbox } from "lucide-react";
import {
  useNotificationSSE,
  useNotificationHistory,
  useMarkRead,
} from "../hooks/useNotifications";
import { cn } from "./ui";

export default function NotificationBell() {
  const { unreadCount } = useNotificationSSE();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef(null);
  const { data: notifications } = useNotificationHistory({ limit: 10 });
  const markRead = useMarkRead();

  useEffect(() => {
    if (!open) return;
    function handleClick(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    function handleKey(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  const items = notifications?.data ?? [];

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Notifications"
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "relative inline-flex h-9 w-9 items-center justify-center rounded-lg",
          "text-text-secondary hover:bg-surface-tertiary hover:text-text-primary",
          "transition-colors duration-150",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink focus-visible:ring-offset-2 focus-visible:ring-offset-surface",
        )}
      >
        <Bell className="h-[18px] w-[18px]" aria-hidden="true" />
        {unreadCount > 0 && (
          <span
            className={cn(
              "absolute right-1 top-1 flex h-4 min-w-4 items-center justify-center",
              "rounded-full bg-swiss-red px-1 text-[10px] font-semibold leading-none text-white",
              "ring-2 ring-surface",
            )}
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          role="menu"
          className={cn(
            "absolute right-0 top-11 z-50 w-80 origin-top-right",
            "animate-scale-in rounded-xl border border-border bg-surface shadow-dropdown",
            "overflow-hidden",
          )}
        >
          <div className="flex items-center justify-between border-b border-border-light px-3.5 py-2.5">
            <span className="text-sm font-semibold text-text-primary">
              Notifications
            </span>
            {unreadCount > 0 && (
              <span className="text-xs text-text-tertiary tabular-nums">
                {unreadCount} new
              </span>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto">
            {items.length > 0 ? (
              items.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => {
                    if (!n.is_read) markRead.mutate(n.id);
                  }}
                  className={cn(
                    "flex w-full gap-3 border-b border-border-light px-3.5 py-3 text-left",
                    "transition-colors duration-150",
                    "hover:bg-surface-tertiary",
                    !n.is_read && "bg-swiss-red-50/60",
                  )}
                >
                  <span
                    className={cn(
                      "mt-1.5 inline-flex h-2 w-2 shrink-0 rounded-full",
                      n.is_read ? "bg-border-strong" : "bg-swiss-red",
                    )}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-text-primary">
                      {n.title}
                    </span>
                    <span className="mt-0.5 line-clamp-2 block text-xs text-text-secondary">
                      {n.body}
                    </span>
                  </span>
                </button>
              ))
            ) : (
              <div className="flex flex-col items-center gap-2 px-3.5 py-8 text-center">
                <Inbox className="h-6 w-6 text-text-quaternary" aria-hidden="true" />
                <p className="text-sm text-text-tertiary">No notifications yet</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

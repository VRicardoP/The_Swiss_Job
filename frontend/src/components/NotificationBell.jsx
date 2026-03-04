import { useState, useRef, useEffect } from "react";
import { useNotificationSSE, useNotificationHistory, useMarkRead } from "../hooks/useNotifications";

export default function NotificationBell() {
  const { unreadCount } = useNotificationSSE();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef(null);
  const { data: notifications } = useNotificationHistory({ limit: 10 });
  const markRead = useMarkRead();

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen(!open)}
        className="relative text-text-secondary hover:text-swiss-red transition-colors duration-200"
        aria-label="Notifications"
      >
        <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
          />
        </svg>
        {unreadCount > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-swiss-red text-[10px] font-bold text-white">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-8 z-50 w-72 bg-surface rounded-xl shadow-dropdown border border-border animate-scale-in">
          <div className="border-b border-border-light px-3 py-2">
            <span className="text-sm font-bold text-text-primary">Notifications</span>
          </div>
          <div className="max-h-64 overflow-y-auto">
            {notifications?.data?.length > 0 ? (
              notifications.data.map((n) => (
                <button
                  key={n.id}
                  onClick={() => {
                    if (!n.is_read) markRead.mutate(n.id);
                  }}
                  className={`w-full border-b border-border-light px-3 py-2 text-left hover:bg-surface-secondary transition-colors duration-150 ${
                    n.is_read ? "opacity-60" : "bg-swiss-red-50"
                  }`}
                >
                  <p className="text-sm font-semibold text-text-primary">{n.title}</p>
                  <p className="text-xs text-text-secondary line-clamp-2">{n.body}</p>
                </button>
              ))
            ) : (
              <p className="px-3 py-4 text-center text-sm text-text-tertiary">
                No notifications yet
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

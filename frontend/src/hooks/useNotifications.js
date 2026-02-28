import { useState, useEffect, useCallback, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { notificationsApi } from "../config/api";
import useAuthStore from "../stores/authStore";

export function useNotificationHistory(params = {}) {
  return useQuery({
    queryKey: ["notifications", params],
    queryFn: () => notificationsApi.list(params),
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => notificationsApi.markRead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useNotificationSSE() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [lastEvent, setLastEvent] = useState(null);
  const token = useAuthStore((s) => s.token);
  const sourceRef = useRef(null);
  const qc = useQueryClient();

  useEffect(() => {
    if (!token) return;

    // Fetch initial unread count
    notificationsApi.list({ limit: 1 }).then((data) => {
      setUnreadCount(data.unread_count);
    }).catch(() => {});

    // SSE connection — pass token as query param (EventSource can't send headers)
    const url = `/api/v1/notifications/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    sourceRef.current = es;

    es.addEventListener("new_matches", (e) => {
      try {
        const data = JSON.parse(e.data);
        setLastEvent(data);
        setUnreadCount((c) => c + 1);
        qc.invalidateQueries({ queryKey: ["notifications"] });
      } catch {}
    });

    es.addEventListener("connected", () => {});

    es.onerror = () => {
      // EventSource auto-reconnects
    };

    return () => {
      es.close();
      sourceRef.current = null;
    };
  }, [token, qc]);

  const resetCount = useCallback(() => setUnreadCount(0), []);

  return { unreadCount, lastEvent, resetCount };
}

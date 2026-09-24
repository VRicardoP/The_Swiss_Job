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

    // H13/T10: el JWT ya NO viaja en la URL. Se pide un vale de un solo uso
    // (Bearer, por la vía normal) que caduca en 30 s.
    //
    // Consecuencia que hay que atender: `EventSource` reconecta solo reusando
    // la MISMA url, y el vale ya está gastado — reconectaría en bucle contra un
    // 401. Por eso la reconexión se hace a mano, pidiendo vale nuevo, con
    // espera creciente para no martillear el servidor si está caído.
    let cancelado = false;
    let temporizador = null;
    let espera = 1000;

    const conectar = async () => {
      if (cancelado) return;
      let ticket;
      try {
        ({ ticket } = await notificationsApi.streamTicket());
      } catch {
        // Sin vale no hay stream; se reintenta. Un 401 aquí ya lo habrá
        // gestionado el cliente de API (refresh o cierre de sesión).
        reintentar();
        return;
      }
      if (cancelado) return;
      const es = new EventSource(
        `/api/v1/notifications/stream?ticket=${encodeURIComponent(ticket)}`,
      );
      sourceRef.current = es;
      preparar(es);
    };

    const reintentar = () => {
      if (cancelado) return;
      temporizador = setTimeout(conectar, espera);
      espera = Math.min(espera * 2, 60000);
    };

    const preparar = (es) => {

    es.addEventListener("new_matches", (e) => {
      try {
        const data = JSON.parse(e.data);
        setLastEvent(data);
        setUnreadCount((c) => c + 1);
        qc.invalidateQueries({ queryKey: ["notifications"] });
      } catch {
        // payload mal formado: ignoramos para que el stream siga vivo
      }
    });

      es.addEventListener("connected", () => {
        espera = 1000; // conexión buena: la espera vuelve a su valor inicial
      });

      es.onerror = () => {
        // El vale ya está gastado, así que dejar reconectar a EventSource sólo
        // daría 401 en bucle: se cierra y se pide uno nuevo.
        es.close();
        if (sourceRef.current === es) sourceRef.current = null;
        reintentar();
      };
    };

    conectar();

    return () => {
      cancelado = true;
      if (temporizador) clearTimeout(temporizador);
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, [token, qc]);

  const resetCount = useCallback(() => setUnreadCount(0), []);

  return { unreadCount, lastEvent, resetCount };
}

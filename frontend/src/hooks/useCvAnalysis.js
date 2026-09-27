import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import useAuthStore from "../stores/authStore";
import { notificationsApi } from "../config/api";

const IDLE = { active: false, percent: 0, message: "", stage: "idle" };

/**
 * Progreso del análisis del CV (autocompletado del perfil).
 *
 * Al llamar a `start()` abre un EventSource al canal SSE del usuario y escucha
 * eventos `cv_analysis_progress` emitidos por la tarea Celery del backend. Cada
 * `start()` reinicia el stream (soporta re-subidas). Al terminar ("done")
 * invalida la query ["profile"] para que la UI muestre los campos ya rellenados.
 */
export function useCvAnalysis() {
  const [state, setState] = useState(IDLE);
  const [session, setSession] = useState(0);
  const token = useAuthStore((s) => s.token);
  const qc = useQueryClient();
  const esRef = useRef(null);

  const start = useCallback(() => {
    setState({
      active: true,
      percent: 5,
      message: "Starting CV analysis…",
      stage: "start",
    });
    setSession((n) => n + 1); // fuerza reconexión del stream en cada subida
  }, []);

  const dismiss = useCallback(() => setState(IDLE), []);

  useEffect(() => {
    if (session === 0 || !token) return;

    // H13/T10: el stream ya no acepta el token en la query — exige un vale de un
    // solo uso. Este consumidor se quedó fuera de aquella migración y seguía
    // mandando `?token=`, que hoy el endpoint rechaza con 422: la barra de
    // progreso del análisis no avanzaba nunca. Se pide el vale igual que
    // `useNotifications`, y como un vale gastado no sirve para reconectar, la
    // reconexión automática de EventSource se desactiva (`es.close()` en error).
    let cancelado = false;
    let es = null;

    const conectar = async () => {
      let ticket;
      try {
        ({ ticket } = await notificationsApi.streamTicket());
      } catch {
        // Sin vale no hay progreso. No se reintenta en bucle: el análisis
        // termina igual en el servidor y la query ["profile"] se recarga al
        // cerrar el banner. Un 401 ya lo gestiona el cliente de API.
        return;
      }
      if (cancelado) return;
      es = new EventSource(
        `/api/v1/notifications/stream?ticket=${encodeURIComponent(ticket)}`,
      );
      esRef.current = es;
      preparar(es);
    };

    const preparar = (es) => {

    es.addEventListener("cv_analysis_progress", (e) => {
      try {
        const d = JSON.parse(e.data);
        setState((s) => ({
          ...s,
          active: true,
          percent: typeof d.percent === "number" ? d.percent : s.percent,
          message: d.message || s.message,
          stage: d.stage || s.stage,
        }));
        if (d.stage === "done") {
          qc.invalidateQueries({ queryKey: ["profile"] });
          es.close();
        } else if (d.stage === "error") {
          es.close();
        }
      } catch {
        // payload mal formado: ignorar para no romper el stream
      }
    });

      // Un vale es de un solo uso, así que la reconexión automática de
      // EventSource iría en bucle contra un 401: se corta.
      es.onerror = () => {
        es.close();
      };
    };

    conectar();

    return () => {
      cancelado = true;
      if (es) es.close();
      esRef.current = null;
    };
  }, [session, token, qc]);

  return { ...state, start, dismiss };
}

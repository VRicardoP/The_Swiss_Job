import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import useAuthStore from "../stores/authStore";

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

    const url = `/api/v1/notifications/stream?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    esRef.current = es;

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

    es.onerror = () => {}; // EventSource reconecta solo

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [session, token, qc]);

  return { ...state, start, dismiss };
}

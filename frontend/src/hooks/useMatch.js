import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { matchApi } from "../config/api";

export function useAnalyze() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => matchApi.analyze(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["match-results"] }),
  });
}

// Carga masiva sin traducciones — solo para categorizar y contar.
export function useMatchResults(limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["match-results", { limit, offset }],
    queryFn: () => matchApi.getResults({ limit, offset, translate: false }),
  });
}

// Carga de una página de resultados con traducciones — para mostrar las tarjetas.


export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash, feedback }) =>
      matchApi.submitFeedback(jobHash, feedback),
    onSuccess: () => {
      // match-results: "not for me" oculta la oferta de la vista de matches.
      // saved-jobs: "Good" la guarda y debe reflejarse al instante en Saved.
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["saved-jobs"] });
    },
  });
}

export function useSubmitImplicit() {
  return useMutation({
    mutationFn: ({ jobHash, action, durationMs }) =>
      matchApi.submitImplicit(jobHash, action, durationMs),
  });
}

export function useClearFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash }) => matchApi.clearFeedback(jobHash),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["match-results"] });
      qc.invalidateQueries({ queryKey: ["saved-jobs"] });
    },
  });
}

export function useSavedJobs(limit = 100, offset = 0) {
  return useQuery({
    queryKey: ["saved-jobs", { limit, offset }],
    // A20-09: como la pantalla principal — títulos ya calentados, sin LLM en
    // el camino de la petición.
    queryFn: () => matchApi.getSaved({ limit, offset, translate: false }),
  });
}

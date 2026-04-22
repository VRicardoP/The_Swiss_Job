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
export function useMatchResultsPage(limit = 100, offset = 0, enabled = true) {
  return useQuery({
    queryKey: ["match-results-page", { limit, offset }],
    queryFn: () => matchApi.getResults({ limit, offset, translate: true }),
    enabled,
    staleTime: 5 * 60 * 1000, // 5 min — las traducciones no cambian frecuentemente
  });
}

export function useMatchHistory(limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["match-history", { limit, offset }],
    queryFn: () => matchApi.getHistory({ limit, offset }),
  });
}

export function useSubmitFeedback() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash, feedback }) =>
      matchApi.submitFeedback(jobHash, feedback),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["match-results"] }),
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
    queryFn: () => matchApi.getSaved({ limit, offset }),
  });
}

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { matchApi } from "../config/api";

export function useAnalyze() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (topK) => matchApi.analyze(topK),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["match-results"] }),
  });
}

export function useMatchResults(limit = 20, offset = 0) {
  return useQuery({
    queryKey: ["match-results", { limit, offset }],
    queryFn: () => matchApi.getResults({ limit, offset }),
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

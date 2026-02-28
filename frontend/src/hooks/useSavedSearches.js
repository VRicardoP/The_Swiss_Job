import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { searchesApi } from "../config/api";

export function useSavedSearches(params = {}) {
  return useQuery({
    queryKey: ["saved-searches", params],
    queryFn: () => searchesApi.list(params),
  });
}

export function useCreateSearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data) => searchesApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-searches"] }),
  });
}

export function useUpdateSearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }) => searchesApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-searches"] }),
  });
}

export function useDeleteSearch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => searchesApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-searches"] }),
  });
}

export function useRunSearch() {
  return useMutation({
    mutationFn: (id) => searchesApi.run(id),
  });
}

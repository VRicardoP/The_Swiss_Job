import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { applicationsApi } from "../config/api";

export function useApplications(params = {}) {
  return useQuery({
    queryKey: ["applications", params],
    queryFn: () => applicationsApi.list(params),
  });
}

export function useApplicationStats() {
  return useQuery({
    queryKey: ["application-stats"],
    queryFn: () => applicationsApi.stats(),
  });
}

export function useCreateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash, notes }) => applicationsApi.create(jobHash, notes),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });
}

export function useUpdateApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }) => applicationsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });
}

export function useDeleteApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) => applicationsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["applications"] });
      qc.invalidateQueries({ queryKey: ["application-stats"] });
    },
  });
}

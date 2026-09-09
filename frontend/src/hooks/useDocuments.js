import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { documentsApi } from "../config/api";
import useAuthStore from "../stores/authStore";

export function useGenerateDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash, docType, language, operationId }) =>
      documentsApi.generateAndFetch(jobHash, docType, language, operationId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useDocumentsForJob(jobHash) {
  const userId = useAuthStore((s) => s.user?.id);
  return useQuery({
    queryKey: ["documents", userId, jobHash],
    queryFn: () => documentsApi.listForJob(jobHash),
    enabled: !!jobHash && !!userId,
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId) => documentsApi.remove(documentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}


export function useDocumentLibrary(cursor, enabled) {
  const userId = useAuthStore((s) => s.user?.id);
  return useQuery({
    queryKey: ["documents", userId, "library", cursor],
    queryFn: () => documentsApi.page(cursor),
    enabled: enabled && !!userId,
  });
}


export function usePendingDocuments(enabled) {
  const userId = useAuthStore((s) => s.user?.id);
  return useQuery({
    queryKey: ["documents", userId, "pending"],
    queryFn: () => documentsApi.pending(),
    enabled: enabled && !!userId,
  });
}

export function useRetryDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (operationId) => documentsApi.retry(operationId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

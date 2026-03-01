import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { documentsApi } from "../config/api";

export function useGenerateDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobHash, docType, language }) =>
      documentsApi.generate(jobHash, docType, language),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["documents", data.job_hash] });
    },
  });
}

export function useDocumentsForJob(jobHash) {
  return useQuery({
    queryKey: ["documents", jobHash],
    queryFn: () => documentsApi.listForJob(jobHash),
    enabled: !!jobHash,
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

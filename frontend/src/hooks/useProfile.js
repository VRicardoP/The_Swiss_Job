import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { profileApi } from "../config/api";

export function useProfile() {
  return useQuery({
    queryKey: ["profile"],
    queryFn: () => profileApi.getProfile(),
    retry: false,
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data) => profileApi.updateProfile(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profile"] }),
  });
}

export function useUploadCV() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file) => profileApi.uploadCV(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profile"] }),
  });
}

export function useDeleteCV() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => profileApi.deleteCV(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profile"] }),
  });
}

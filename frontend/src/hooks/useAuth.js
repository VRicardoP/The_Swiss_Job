import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authApi } from "../config/api";
import useAuthStore from "../stores/authStore";

export function useAuthHydration() {
  const token = useAuthStore((s) => s.token);
  const setUser = useAuthStore((s) => s.setUser);
  const setHydrated = useAuthStore((s) => s.setHydrated);

  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => authApi.getMe(),
    enabled: !!token,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    if (!token) {
      setHydrated(true);
      return;
    }
    if (query.isSuccess) {
      setUser(query.data);
      setHydrated(true);
    }
    if (query.isError) {
      useAuthStore.getState().logout();
    }
  }, [token, query.isSuccess, query.isError, query.data, setUser, setHydrated]);

  return query;
}

export function useLogin() {
  const setAuth = useAuthStore((s) => s.setAuth);
  const setHydrated = useAuthStore((s) => s.setHydrated);
  const navigate = useNavigate();

  return useMutation({
    mutationFn: ({ email, password }) => authApi.login(email, password),
    onSuccess: (data) => {
      setAuth(data.access_token, data.refresh_token, data.user);
      setHydrated(true);
      navigate("/profile");
    },
  });
}

export function useRegister() {
  const setAuth = useAuthStore((s) => s.setAuth);
  const setHydrated = useAuthStore((s) => s.setHydrated);
  const navigate = useNavigate();

  return useMutation({
    mutationFn: ({ email, password, gdpr_consent }) =>
      authApi.register(email, password, gdpr_consent),
    onSuccess: (data) => {
      setAuth(data.access_token, data.refresh_token, data.user);
      setHydrated(true);
      navigate("/profile");
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  return () => {
    logout();
    qc.removeQueries({ queryKey: ["auth"] });
    qc.removeQueries({ queryKey: ["profile"] });
    navigate("/");
  };
}

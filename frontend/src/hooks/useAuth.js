import { useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { authApi, endsSession } from "../config/api";
import useAuthStore from "../stores/authStore";

/**
 * C6: qué hacer cuando la hidratación de la sesión falla.
 *
 * Vive fuera del hook para poder probarla: sólo un 401/403 significa que la
 * credencial murió. Con cualquier otro error —500, 502, backend caído— la
 * sesión se conserva y se marca hidratado, para no dejar la app en blanco
 * esperando eternamente. Antes, un 502 de un segundo deslogueaba al usuario.
 */
export function resolverErrorDeSesion(status, { logout, setHydrated }) {
  if (endsSession(status)) logout();
  else setHydrated(true);
}

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
      resolverErrorDeSesion(query.error?.status, {
        logout: useAuthStore.getState().logout,
        setHydrated,
      });
    }
  }, [
    token,
    query.isSuccess,
    query.isError,
    query.error,
    query.data,
    setUser,
    setHydrated,
  ]);

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

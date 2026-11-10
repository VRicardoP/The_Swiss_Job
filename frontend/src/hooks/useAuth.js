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

/**
 * A-11: el cierre de sesión completo — revocar en el SERVIDOR y limpiar local.
 *
 * Vive fuera del hook por el mismo motivo que `resolverErrorDeSesion`: para
 * poder probarlo. Un test que llame a `authApi.logout` por su cuenta no
 * demuestra nada, porque seguiría pasando si alguien borrase la llamada de
 * aquí — que es justo la regresión que hay que impedir.
 *
 * Hasta el 2026-11-04 este primer paso NO EXISTÍA, y por eso T9 entero —jti,
 * fam, canje único, caída de familia— era mecanismo muerto: el endpoint y su
 * maquinaria estaban construidos y probados desde septiembre, pero ningún
 * cliente los invocaba. Cerrar sesión sólo borraba el `localStorage`, así que
 * el refresh seguía canjeable `REFRESH_TOKEN_EXPIRE_DAYS` (7 días) desde el
 * navegador de un ordenador compartido.
 *
 * El token se lee ANTES de `logout()`, que lo borra. Y la revocación NO se
 * espera a propósito: el endpoint es idempotente y contesta 204 incluso con un
 * token ilegible o ya revocado, así que no hay nada que decidir con su
 * respuesta; y ante un fallo de red lo correcto es cerrar en local igualmente.
 */
export function cerrarSesion({ logout, qc, navigate }) {
  const refreshToken = useAuthStore.getState().refreshToken;
  if (refreshToken) authApi.logout(refreshToken).catch(() => {});

  logout();
  // DT-148 A6: se borraban SOLO "auth" y "profile". Matches, candidaturas,
  // documentos, notificaciones y borradores seguían en caché con su staleTime,
  // así que la siguiente cuenta que entrase en el mismo navegador veía datos de
  // la anterior. Se vacía todo: nada de la sesión que acaba sobrevive a su
  // cierre.
  qc.clear();
  navigate("/");
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

  return () => cerrarSesion({ logout, qc, navigate });
}

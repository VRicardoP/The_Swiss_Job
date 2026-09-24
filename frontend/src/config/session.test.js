/**
 * C6 — la sesión sobrevive a lo transitorio y sólo muere con 401/403.
 *
 * Antes: no había refresh en ninguna parte, así que la sesión se acababa a los
 * 30 minutos (la vida del access token) y había que volver a entrar a mano; y
 * `useAuthHydration` llamaba a `logout()` ante CUALQUIER error, de modo que un
 * 502 de un segundo borraba los tokens del navegador.
 *
 * Cada prueba de abajo falla por SU propia condición: que el 401 reintente, que
 * N peticiones provoquen UN refresh, que un 500 no toque la sesión, y que sólo
 * un refresh rechazado la cierre.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applicationsApi, endsSession, profileApi } from "./api";
import useAuthStore from "../stores/authStore";
import { resolverErrorDeSesion } from "../hooks/useAuth";

function sesion(token = "viejo", refreshToken = "rt-1") {
  useAuthStore.getState().setAuth(token, refreshToken, { id: "u1" });
}

function autorizacionDe(llamada) {
  return llamada[1]?.headers?.Authorization;
}

beforeEach(() => sesion());
afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().logout();
});

describe("renovación de la sesión ante un 401", () => {
  it("renueva y reintenta con el token NUEVO", async () => {
    const fetchMock = vi
      .fn()
      // 1) la petición original caduca
      .mockResolvedValueOnce(Response.json({ detail: "expired" }, { status: 401 }))
      // 2) el refresh devuelve pareja nueva
      .mockResolvedValueOnce(
        Response.json({
          access_token: "nuevo",
          refresh_token: "rt-2",
          user: { id: "u1" },
        }),
      )
      // 3) el reintento
      .mockResolvedValueOnce(Response.json({ data: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(applicationsApi.list()).resolves.toEqual({ data: [], total: 0 });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(autorizacionDe(fetchMock.mock.calls[0])).toBe("Bearer viejo");
    expect(fetchMock.mock.calls[1][0]).toContain("/auth/refresh");
    expect(autorizacionDe(fetchMock.mock.calls[2])).toBe("Bearer nuevo");
    // la rotación se persiste: el refresh token también es el nuevo
    expect(useAuthStore.getState().refreshToken).toBe("rt-2");
  });

  it("no reintenta dos veces: si el reintento también da 401, se rinde", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 401 }))
      .mockResolvedValueOnce(
        Response.json({ access_token: "nuevo", refresh_token: "rt-2" }),
      )
      .mockResolvedValueOnce(Response.json({}, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(applicationsApi.list()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(3); // no hay bucle infinito
  });

  it("N peticiones en vuelo provocan UN solo refresh", async () => {
    const fetchMock = vi.fn().mockImplementation((url) => {
      if (String(url).includes("/auth/refresh")) {
        return Promise.resolve(
          Response.json({ access_token: "nuevo", refresh_token: "rt-2" }),
        );
      }
      const cabecera = fetchMock.mock.calls.at(-1)[1]?.headers?.Authorization;
      return Promise.resolve(
        cabecera === "Bearer nuevo"
          ? Response.json({ ok: true })
          : Response.json({}, { status: 401 }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      applicationsApi.list(),
      applicationsApi.stats(),
      profileApi.getProfile(),
    ]);

    const refrescos = fetchMock.mock.calls.filter((c) =>
      String(c[0]).includes("/auth/refresh"),
    );
    expect(refrescos).toHaveLength(1);
  });
});

describe("lo transitorio NO cierra la sesión", () => {
  it.each([500, 502, 503, 504])(
    "un %i se propaga sin renovar ni desloguear",
    async (status) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(Response.json({ detail: "caído" }, { status }));
      vi.stubGlobal("fetch", fetchMock);

      await expect(applicationsApi.list()).rejects.toMatchObject({ status });
      expect(fetchMock).toHaveBeenCalledTimes(1); // ni refresh ni reintento
      expect(useAuthStore.getState().token).toBe("viejo");
    },
  );

  it("un fallo de red tampoco la cierra", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(applicationsApi.list()).rejects.toThrow("Failed to fetch");
    expect(useAuthStore.getState().token).toBe("viejo");
  });

  it("si el refresh falla por un 500, la sesión SOBREVIVE", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 401 }))
      .mockResolvedValueOnce(Response.json({ detail: "db caída" }, { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(applicationsApi.list()).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().token).toBe("viejo");
    expect(useAuthStore.getState().refreshToken).toBe("rt-1");
  });
});

describe("lo definitivo SÍ la cierra", () => {
  it("un refresh rechazado con 401 borra los tokens", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 401 }))
      .mockResolvedValueOnce(Response.json({ detail: "invalid" }, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(applicationsApi.list()).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it("sin refresh token guardado, el 401 cierra sin pedir nada", async () => {
    useAuthStore.setState({ refreshToken: null });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(applicationsApi.list()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(1); // no se intenta refrescar
    expect(useAuthStore.getState().token).toBeNull();
  });
});

describe("las rutas con fetch crudo también renuevan", () => {
  it("la subida de CV reintenta tras renovar, y sin Content-Type", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 401 }))
      .mockResolvedValueOnce(
        Response.json({ access_token: "nuevo", refresh_token: "rt-2" }),
      )
      .mockResolvedValueOnce(Response.json({ filename: "cv.pdf" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(profileApi.uploadCV(new Blob(["x"]))).resolves.toEqual({
      filename: "cv.pdf",
    });
    expect(autorizacionDe(fetchMock.mock.calls[2])).toBe("Bearer nuevo");
    // el boundary lo pone el navegador: fijar Content-Type aquí lo rompería
    expect(fetchMock.mock.calls[2][1].headers["Content-Type"]).toBeUndefined();
  });
});

describe("endsSession es la única definición de «sesión terminada»", () => {
  it.each([401, 403])("%i la termina", (s) => expect(endsSession(s)).toBe(true));
  it.each([400, 404, 409, 429, 500, 502, 503, undefined])(
    "%s no la termina",
    (s) => expect(endsSession(s)).toBe(false),
  );
});

describe("hidratación: qué pasa cuando /auth/me falla", () => {
  function espias() {
    return { logout: vi.fn(), setHydrated: vi.fn() };
  }

  it.each([401, 403])("un %i cierra la sesión", (status) => {
    const s = espias();
    resolverErrorDeSesion(status, s);
    expect(s.logout).toHaveBeenCalledOnce();
    expect(s.setHydrated).not.toHaveBeenCalled();
  });

  it.each([500, 502, 503, undefined])(
    "un %s la conserva y marca hidratado",
    (status) => {
      const s = espias();
      resolverErrorDeSesion(status, s);
      expect(s.logout).not.toHaveBeenCalled();
      expect(s.setHydrated).toHaveBeenCalledWith(true);
    },
  );
});

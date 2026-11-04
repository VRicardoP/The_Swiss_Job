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
import { authApi, endsSession, profileApi, searchesApi } from "./api";
import { registrarVaciadoDeSesion } from "./sessionTeardown";
import useAuthStore, { manejarCambioDeAlmacen } from "../stores/authStore";
import { cerrarSesion, resolverErrorDeSesion } from "../hooks/useAuth";

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

    await expect(searchesApi.list()).resolves.toEqual({ data: [], total: 0 });

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

    await expect(searchesApi.list()).rejects.toMatchObject({ status: 401 });
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
      searchesApi.list(),
      profileApi.getProfile(),
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

      await expect(searchesApi.list()).rejects.toMatchObject({ status });
      expect(fetchMock).toHaveBeenCalledTimes(1); // ni refresh ni reintento
      expect(useAuthStore.getState().token).toBe("viejo");
    },
  );

  it("un fallo de red tampoco la cierra", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(searchesApi.list()).rejects.toThrow("Failed to fetch");
    expect(useAuthStore.getState().token).toBe("viejo");
  });

  it("si el refresh falla por un 500, la sesión SOBREVIVE", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({}, { status: 401 }))
      .mockResolvedValueOnce(Response.json({ detail: "db caída" }, { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchesApi.list()).rejects.toMatchObject({ status: 401 });
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

    await expect(searchesApi.list()).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().refreshToken).toBeNull();
  });

  it("sin refresh token guardado, el 401 cierra sin pedir nada", async () => {
    useAuthStore.setState({ refreshToken: null });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchesApi.list()).rejects.toMatchObject({ status: 401 });
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

describe("cerrar sesión revoca la familia en el SERVIDOR (A-11)", () => {
  // Hasta el 2026-11-04 `authApi` no tenía `logout` y nadie llamaba al
  // endpoint, así que T9 entero —jti, fam, canje único, caída de familia—
  // existía, estaba probado en el backend y NO SERVÍA PARA NADA: cerrar sesión
  // sólo borraba el localStorage y el refresh seguía canjeable 7 días.
  //
  // Se prueba `cerrarSesion`, que es el camino que `useLogout` ejecuta, NO
  // `authApi.logout` por su cuenta: un test de lo segundo seguiría verde si
  // alguien borrase la llamada, que es exactamente la regresión a impedir.

  function dobles() {
    return { logout: vi.fn(), qc: { clear: vi.fn() }, navigate: vi.fn() };
  }

  it("manda el refresh token a /auth/logout y limpia en local", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const d = dobles();

    cerrarSesion(d);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const [url, opciones] = fetchMock.mock.calls[0];
    expect(url).toContain("/auth/logout");
    expect(opciones.method).toBe("POST");
    expect(JSON.parse(opciones.body)).toEqual({ refresh_token: "rt-1" });
    // y el cierre local ocurre igual, sin esperar al servidor
    expect(d.logout).toHaveBeenCalled();
    expect(d.qc.clear).toHaveBeenCalled();
    expect(d.navigate).toHaveBeenCalledWith("/");
  });

  it("no manda el access token: debe funcionar con el access ya caducado", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    cerrarSesion(dobles());
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // `request` y no `authRequest`: sin cabecera Authorization. Es el contrato
    // del endpoint, que no exige access token a propósito.
    expect(autorizacionDe(fetchMock.mock.calls[0])).toBeUndefined();
  });

  it("un fallo de red al revocar NO impide cerrar en local", async () => {
    // Lo que no puede pasar es que un error de red deje al usuario con la
    // sesión abierta en el navegador. Si la promesa quedase sin capturar, este
    // caso saldría con un unhandled rejection.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const d = dobles();

    expect(() => cerrarSesion(d)).not.toThrow();
    expect(d.logout).toHaveBeenCalled();
    expect(d.qc.clear).toHaveBeenCalled();
  });

  it("sin refresh token guardado no se llama al endpoint, pero se limpia igual", async () => {
    useAuthStore.getState().logout();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const d = dobles();

    cerrarSesion(d);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(d.qc.clear).toHaveBeenCalled();
  });

  it("el 204 sin cuerpo no revienta al parsear", async () => {
    // `request` devuelve undefined en un 204; si intentase `res.json()` el
    // cierre acabaría en una promesa rechazada.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    await expect(authApi.logout("rt-1")).resolves.toBeUndefined();
  });
});

describe("tokens rotados por OTRA pestaña (A-12)", () => {
  // El store leía localStorage UNA sola vez, al cargarse el módulo, y nunca
  // volvía a mirar. Pero el almacén es compartido y el refresh ROTA: cuando la
  // pestaña A refresca, B se queda con el viejo EN MEMORIA y al caducar su
  // access lo presenta. El servidor lo ve reaparecer, no puede saber si es un
  // ladrón, y caía la familia: las dos pestañas fuera.
  //
  // El arnés corre sin DOM (A-69), así que se simula `localStorage` y se llama
  // al manejador exportado en vez de despachar un evento real.
  //
  // Control negativo: quitando `sincronizarDesdeAlmacen()` de `refreshSession`,
  // el primer caso presenta el token rancio y falla.

  function almacenSimulado(inicial = {}) {
    const datos = { ...inicial };
    vi.stubGlobal("localStorage", {
      getItem: (k) => (k in datos ? datos[k] : null),
      setItem: (k, v) => {
        datos[k] = String(v);
      },
      removeItem: (k) => {
        delete datos[k];
      },
    });
    return datos;
  }

  it("el refresh usa el token que dejó la otra pestaña, no el de memoria", async () => {
    sesion("viejo", "rt-rancio");
    // La otra pestaña ya rotó: el almacén lleva el nuevo y la memoria el viejo.
    almacenSimulado({ swissjob_refresh_token: "rt-de-la-otra-pestana" });
    expect(useAuthStore.getState().refreshToken).toBe("rt-rancio");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ detail: "expired" }, { status: 401 }))
      .mockResolvedValueOnce(
        Response.json({
          access_token: "nuevo",
          refresh_token: "rt-3",
          user: { id: "u1" },
        }),
      )
      .mockResolvedValueOnce(Response.json({ data: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await searchesApi.list();

    const cuerpoDelRefresh = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(cuerpoDelRefresh.refresh_token).toBe("rt-de-la-otra-pestana");
  });

  it("el cambio de almacén rehidrata el store", () => {
    sesion("viejo", "rt-1");
    almacenSimulado({
      swissjob_token: "token-de-la-otra",
      swissjob_refresh_token: "rt-de-la-otra",
    });

    manejarCambioDeAlmacen({ key: "swissjob_token" });

    expect(useAuthStore.getState().token).toBe("token-de-la-otra");
    expect(useAuthStore.getState().refreshToken).toBe("rt-de-la-otra");
  });

  it("una clave ajena NO rehidrata nada", () => {
    sesion("viejo", "rt-1");
    almacenSimulado({ swissjob_token: "no-deberia-leerse" });

    manejarCambioDeAlmacen({ key: "otra_cosa" });

    expect(useAuthStore.getState().token).toBe("viejo");
  });

  it("sin localStorage no revienta ni pisa la memoria", () => {
    // El entorno sin DOM es el del propio arnés, y antes del refactor dejaba
    // esta función inerte para siempre por capturar el almacén al importar.
    sesion("viejo", "rt-1");
    useAuthStore.getState().sincronizarDesdeAlmacen();
    expect(useAuthStore.getState().token).toBe("viejo");
  });
});

describe("el cierre INVOLUNTARIO también vacía la caché (A-28)", () => {
  // `useLogout` hacía `qc.clear()` y promete que «nada de la sesión que acaba
  // sobrevive a su cierre». El cierre involuntario —cuando el propio refresh
  // devuelve 401/403— iba por otro camino y dejaba la caché dentro: matches,
  // perfil, guardados, notificaciones y borradores de la cuenta que salía,
  // visibles para la siguiente cuenta en el mismo navegador durante su
  // staleTime de 2 min.
  //
  // Control negativo: quitando `queryClient.clear()` de `renovarSesion`, este
  // caso encuentra el dato de la cuenta anterior todavía en la caché.

  it("un refresh rechazado con 401 deja la caché VACÍA", async () => {
    sesion("viejo", "rt-1");
    const vaciados = [];
    registrarVaciadoDeSesion(() => vaciados.push("vaciado"));

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ detail: "expired" }, { status: 401 }))
        .mockResolvedValueOnce(Response.json({ detail: "invalid" }, { status: 401 })),
    );

    await expect(searchesApi.list()).rejects.toThrow();

    expect(useAuthStore.getState().token).toBeNull();
    expect(vaciados).toEqual(["vaciado"]);
  });

  it("un fallo TRANSITORIO no vacía la caché", async () => {
    // La contraparte que impide pasarse de celoso: un 500 o un corte de red no
    // terminan la sesión, así que tampoco pueden tirar los datos.
    sesion("viejo", "rt-1");
    const vaciados = [];
    registrarVaciadoDeSesion(() => vaciados.push("vaciado"));

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(Response.json({ detail: "expired" }, { status: 401 }))
        .mockResolvedValueOnce(Response.json({ detail: "boom" }, { status: 500 })),
    );

    await expect(searchesApi.list()).rejects.toThrow();

    expect(useAuthStore.getState().token).toBe("viejo");
    expect(vaciados).toEqual([]);
  });
});

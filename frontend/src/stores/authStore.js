import { create } from "zustand";

const TOKEN_KEY = "swissjob_token";
const REFRESH_KEY = "swissjob_refresh_token";

// Se resuelve en CADA uso en vez de capturarse al cargar el módulo. La captura
// era frágil por dos motivos: en un entorno sin DOM dejaba `storage` en null
// para siempre, de modo que cualquier función que lo usara quedaba inerte y sin
// forma de probarse; y ataba el store al estado del global en el instante del
// import, que es justo el momento menos controlable.
function almacen() {
  return typeof localStorage !== "undefined" ? localStorage : null;
}

const useAuthStore = create((set) => ({
  token: almacen()?.getItem(TOKEN_KEY) ?? null,
  refreshToken: almacen()?.getItem(REFRESH_KEY) ?? null,
  user: null,
  hydrated: false,

  /**
   * A-12, mitad del frontend: rehidrata los tokens que ROTÓ otra pestaña.
   *
   * El store los leía UNA sola vez, al cargarse el módulo, y nunca volvía a
   * mirar `localStorage`. Pero el almacén es compartido entre pestañas y el
   * refresh ROTA: cuando la pestaña A refresca, B se queda con el token viejo
   * EN MEMORIA y lo presenta al caducar su access. El servidor lo ve reaparecer
   * y no puede saber si es un ladrón, así que caía la familia y las dos
   * pestañas quedaban fuera.
   *
   * El servidor tiene ya su ventana de gracia para esa carrera, pero esto es la
   * mitad que de verdad la evita: si B tiene el token actual, no hay carrera
   * que perdonar.
   */
  sincronizarDesdeAlmacen() {
    const s = almacen();
    if (!s) return;
    set({
      token: s.getItem(TOKEN_KEY) ?? null,
      refreshToken: s.getItem(REFRESH_KEY) ?? null,
    });
  },

  setAuth(token, refreshToken, user = null) {
    almacen()?.setItem(TOKEN_KEY, token);
    almacen()?.setItem(REFRESH_KEY, refreshToken);
    set({ token, refreshToken, user });
  },

  setUser(user) {
    set({ user });
  },

  setHydrated(hydrated) {
    set({ hydrated });
  },

  logout() {
    almacen()?.removeItem(TOKEN_KEY);
    almacen()?.removeItem(REFRESH_KEY);
    set({ token: null, refreshToken: null, user: null, hydrated: true });
  },
}));

/**
 * Manejador del evento `storage`. Exportado para poder probarlo: el arnés del
 * frontend corre sin DOM (A-69), así que registrarlo en `window` y no exponerlo
 * lo dejaría sin cobertura. Es el mismo motivo por el que
 * `resolverErrorDeSesion` y `cerrarSesion` viven fuera de su hook.
 *
 * Se filtra por clave para no rehidratar por cualquier otra cosa que el
 * navegador guarde.
 */
export function manejarCambioDeAlmacen(evento) {
  if (evento?.key === TOKEN_KEY || evento?.key === REFRESH_KEY) {
    useAuthStore.getState().sincronizarDesdeAlmacen();
  }
}

// El navegador entrega este evento sólo a las OTRAS pestañas, nunca a la que
// escribió: es exactamente la señal que hace falta.
if (typeof window !== "undefined") {
  window.addEventListener("storage", manejarCambioDeAlmacen);
}

export default useAuthStore;

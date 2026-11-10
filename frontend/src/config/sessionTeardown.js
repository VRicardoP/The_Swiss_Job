/**
 * Lo que hay que limpiar cuando una sesión termina, registrado desde fuera.
 *
 * Existe para que `config/api.js` pueda vaciar la caché de react-query SIN
 * depender de react-query. El primer intento fue importar el `QueryClient`
 * desde un módulo compartido, y tenía dos defectos que sólo se vieron al
 * ejecutar: rompía `useAnalytics.test.js`, que simula `@tanstack/react-query`
 * sin exportar `QueryClient`, y —peor— convertía la construcción del cliente en
 * un efecto de importar `api.js`, que lo importa casi todo el frontend.
 *
 * Con el registro, `api.js` sólo conoce una función. Quién la rellena y con qué
 * es decisión de `main.jsx`, que es el único sitio que ya conoce react-query.
 */
let vaciar = () => {};

export function registrarVaciadoDeSesion(fn) {
  vaciar = typeof fn === "function" ? fn : () => {};
}

export function vaciarEstadoDeSesion() {
  vaciar();
}

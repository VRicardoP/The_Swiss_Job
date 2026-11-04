import { AlertTriangle } from "lucide-react";
import EmptyState from "./EmptyState";

/**
 * Los TRES estados de una lectura, distinguidos: cargando, error y vacío.
 *
 * Por qué existe (A-31): ocho sitios del frontend no consumían `isError`, así
 * que un fallo de red se pintaba con el mensaje del vacío — «Aún no hay
 * vacantes en la watchlist», «No active filters», «No notifications yet». Un
 * error disfrazado de vacío es peor que un error: el usuario no reintenta,
 * porque cree que no hay nada, y el que mira el problema tampoco lo ve.
 *
 * Y había un caso peor que el cosmético: `FiltersPage` mostraba «No active
 * filters» ante un fallo de carga, de modo que el usuario podía recrear filtros
 * que YA existían.
 *
 * Se resuelve con un componente y no parcheando ocho `if` iguales, porque lo
 * que hay que fijar es la regla —tres estados, nunca dos— y no ocho sitios.
 *
 * El error lleva `role="alert"`, copiado de `DocumentGenerator`, que era el
 * único del repo que lo hacía bien: sin eso un lector de pantalla no anuncia
 * nada y el fallo es invisible dos veces.
 */
export default function QueryState({
  query,
  cargando = null,
  vacio = null,
  children,
  // Qué cuenta como «hay datos». Por defecto, que `data` traiga algo; una lista
  // vacía NO cuenta, que es el caso habitual.
  tieneDatos,
}) {
  if (query?.isLoading) {
    return cargando;
  }

  if (query?.isError) {
    // El `role="alert"` va en una envoltura porque `EmptyState` no lo acepta y
    // no es asunto suyo: él pinta un hueco, y un hueco no siempre es un error.
    return (
      <div role="alert">
        <EmptyState
          icon={AlertTriangle}
          title="No se pudo cargar"
          description={
            query.error?.message ||
            "Ha fallado la carga. Recarga la página para volver a intentarlo."
          }
        />
      </div>
    );
  }

  const hay =
    typeof tieneDatos === "boolean"
      ? tieneDatos
      : Array.isArray(query?.data)
        ? query.data.length > 0
        : query?.data != null;

  return hay ? children : vacio;
}

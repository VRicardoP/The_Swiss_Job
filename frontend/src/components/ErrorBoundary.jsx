import { Component } from "react";
import { Button } from "./ui";

/**
 * Red de seguridad para errores de RENDER.
 *
 * Por qué existe: el router es `<BrowserRouter>` + `<Routes>` (el NO-data
 * router), que no admite `errorElement`. Sin un boundary, cualquier excepción
 * lanzada durante el render de CUALQUIER página sube hasta la raíz de React y
 * desmonta el árbol completo: el usuario ve una pantalla EN BLANCO, sin ningún
 * indicio de qué pasó. Ocurrió de verdad — `/match` estuvo así 13 días en
 * producción por leer una propiedad de un objeto aún no cargado dentro de un
 * array de dependencias.
 *
 * Va POR RUTA y no una sola vez en la raíz a propósito: así un fallo en una
 * página deja el Navbar y la navegación en pie, y el usuario puede irse a otra
 * parte en vez de quedarse sin aplicación.
 *
 * No captura errores de eventos ni de promesas (React no los enruta aquí); para
 * esos está el manejo de `isError` de cada query.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // La consola del navegador es el único destino disponible: no hay
    // telemetría de frontend en este proyecto, y añadirla es otra decisión.
    console.error("Error de render no capturado:", error, info?.componentStack);
  }

  // Al navegar a otra ruta, React monta un boundary nuevo (la `key` del
  // consumidor es la ruta), así que no hace falta limpiar el estado aquí.

  render() {
    if (this.state.error === null) return this.props.children;

    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <div
          role="alert"
          className="rounded-lg border border-error-border bg-error-light p-6"
        >
          <h1 className="text-lg font-semibold text-error">
            Something went wrong on this page
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            The rest of the app still works — use the navigation to go
            somewhere else, or reload this page to try again.
          </p>
          {/* El mensaje técnico se muestra porque esta aplicación la usa su
              propio dueño: ocultarlo sólo haría el diagnóstico más lento. */}
          <p className="mt-3 break-words font-mono text-xs text-text-tertiary">
            {this.state.error?.message || String(this.state.error)}
          </p>
          <Button
            variant="secondary"
            className="mt-4"
            onClick={() => window.location.reload()}
          >
            Reload page
          </Button>
        </div>
      </div>
    );
  }
}

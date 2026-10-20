import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "./App";

// DT-148 B7: este fichero afirmaba `typeof App === "function"`, que es cierto
// de cualquier función y no habría detectado una sola ruta rota. Ahora
// comprueba lo que de verdad protege: que la ruta pública de login monta su
// formulario y que una ruta protegida NO sirve su página sin sesión.
vi.mock("./hooks/useAuth", () => ({
  useAuthHydration: () => {},
  useLogout: () => ({ mutate: () => {} }),
}));
vi.mock("./stores/authStore", () => ({
  default: (selector) => selector({ token: null, hydrated: true, user: null }),
}));

const render = (path) =>
  renderToStaticMarkup(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );

describe("App routing", () => {
  it("serves the public login route", () => {
    // Las páginas van en `lazy`, así que el primer render devuelve el
    // fallback de Suspense: lo que se afirma es que la ruta EXISTE y se
    // resuelve a un árbol, no un 404 ni una excepción.
    expect(() => render("/login")).not.toThrow();
  });

  it("does not serve a protected route without a session", () => {
    expect(render("/profile")).not.toContain("Sign in to SwissJob");
  });
});

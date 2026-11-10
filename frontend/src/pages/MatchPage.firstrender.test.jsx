import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import MatchPage from "./MatchPage";

/**
 * Regresión de la pantalla principal EN BLANCO (auditoría 2026-11-02, A-01).
 *
 * `/match` lanzaba `TypeError: Cannot read properties of undefined (reading
 * 'top_score')` en el PRIMER render y, al no haber ErrorBoundary ni
 * `errorElement` (el router es el no-data), React desmontaba el árbol entero:
 * pantalla en blanco. Estuvo 13 días así en producción, y sólo funcionaba si se
 * visitaba /watchlist antes, porque eso llenaba la caché `["match-facets"]`.
 *
 * La causa era sutil: `facets.top_score` estaba dentro del ARRAY DE
 * DEPENDENCIAS de un `useMemo`, y un array de dependencias se construye en cada
 * render al margen del ternario que protege el cuerpo. Por eso el guard
 * `facetsEnabled ? … : …` no bastaba.
 *
 * Lo que este test fija es el estado que ninguna prueba cubría: el primer
 * render, con las queries SIN datos. `renderToStaticMarkup` no ejecuta efectos,
 * pero sí ejecuta el cuerpo del componente y sus `useMemo` — que es exactamente
 * donde estaba el fallo —, así que no hace falta jsdom para cubrirlo.
 *
 * Control negativo comprobado: devolviendo `facets.top_score` al array de
 * dependencias, los tres primeros casos fallan con el TypeError original.
 */

const estado = vi.hoisted(() => ({ facets: undefined, perfil: undefined }));

vi.mock("../hooks/useMatch", () => ({
  useMatchFacets: () => estado.facets,
  useMatchResults: () => ({ data: undefined, isLoading: false, isError: false, error: null }),
  useAnalyze: () => ({ mutate: () => {}, isPending: false }),
  useSubmitFeedback: () => ({ mutate: () => {} }),
  useClearFeedback: () => ({ mutate: () => {} }),
  useSubmitImplicit: () => ({ mutate: () => {} }),
}));

vi.mock("../hooks/useProfile", () => ({
  useProfile: () => estado.perfil,
}));

const pintar = () =>
  renderToStaticMarkup(
    <MemoryRouter>
      <MatchPage />
    </MemoryRouter>,
  );

describe("MatchPage · primer render", () => {
  it("se pinta mientras la petición de facetas está EN VUELO", () => {
    // El estado real de la primera carga: data undefined, isSuccess false.
    estado.facets = { data: undefined, isSuccess: false, isError: false, isLoading: true };
    estado.perfil = { data: undefined, isLoading: true };
    expect(() => pintar()).not.toThrow();
  });

  it("se pinta cuando las facetas FALLAN (modo legacy)", () => {
    estado.facets = { data: undefined, isSuccess: false, isError: true, isLoading: false };
    estado.perfil = { data: { has_cv_embedding: true }, isLoading: false };
    expect(() => pintar()).not.toThrow();
  });

  it("se pinta con facetas resueltas pero vacías", () => {
    estado.facets = { data: undefined, isSuccess: false, isError: false, isLoading: false };
    estado.perfil = { data: { has_cv_embedding: false }, isLoading: false };
    expect(() => pintar()).not.toThrow();
  });

  it("usa los agregados del servidor cuando las facetas SÍ llegan", () => {
    estado.facets = {
      data: { total: 7, top_score: 91.4, strong_count: 3, watchlist_count: 0, categories: [] },
      isSuccess: true,
      isError: false,
      isLoading: false,
    };
    estado.perfil = { data: { has_cv_embedding: true }, isLoading: false };
    const html = pintar();
    // 91.4 se redondea a 91: prueba que el valor viaja hasta el tile y que la
    // extracción del escalar no rompió el camino bueno.
    expect(html).toContain("91");
  });
});

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import QueryState from "./QueryState";

/**
 * A-31 — un fallo de red se pintaba con el mensaje del VACÍO.
 *
 * Ocho sitios del frontend no consumían `isError`, así que un error de carga
 * salía como «Aún no hay vacantes en la watchlist», «No active filters» o «No
 * notifications yet». Un error disfrazado de vacío es peor que un error: el
 * usuario no reintenta, porque cree que no hay nada, y quien mira el problema
 * tampoco lo ve.
 *
 * Y dos de los ocho no eran cosméticos: «No active filters» ante un fallo
 * llevaba a recrear filtros que YA existían, y en Onboarding el formulario
 * vacío terminaba en un PUT destructivo sobre el perfil real (A-27).
 */

const pintar = (ui) => renderToStaticMarkup(<MemoryRouter>{ui}</MemoryRouter>);

describe("QueryState · los tres estados se distinguen", () => {
  it("cargando: pinta lo que se le dé y NADA más", () => {
    const html = pintar(
      <QueryState
        query={{ isLoading: true }}
        cargando={<p>Cargando…</p>}
        vacio={<p>No hay nada</p>}
      >
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain("Cargando…");
    expect(html).not.toContain("No hay nada");
    expect(html).not.toContain("datos");
  });

  it("error: NO pinta el mensaje del vacío — es el defecto que cierra A-31", () => {
    const html = pintar(
      <QueryState
        query={{ isError: true, error: new Error("Failed to fetch") }}
        vacio={<p>Aún no hay vacantes en la watchlist</p>}
      >
        <p>datos</p>
      </QueryState>,
    );
    expect(html).not.toContain("Aún no hay vacantes");
    expect(html).toContain("No se pudo cargar");
    expect(html).toContain("Failed to fetch");
  });

  it('error: lleva role="alert" para que se anuncie', () => {
    // Sin esto un lector de pantalla no dice nada y el fallo es invisible dos
    // veces. Copiado de DocumentGenerator, el único del repo que lo hacía bien.
    const html = pintar(
      <QueryState query={{ isError: true, error: new Error("boom") }}>
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain('role="alert"');
  });

  it("error sin mensaje: dice algo accionable en vez de quedarse en blanco", () => {
    const html = pintar(
      <QueryState query={{ isError: true, error: {} }}>
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain("Recarga la página");
  });

  it("vacío: pinta el hueco, que es un estado legítimo", () => {
    const html = pintar(
      <QueryState query={{ data: [] }} vacio={<p>No hay nada</p>}>
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain("No hay nada");
    expect(html).not.toContain("No se pudo cargar");
  });

  it("con datos: pinta los hijos", () => {
    const html = pintar(
      <QueryState query={{ data: [1, 2] }} vacio={<p>No hay nada</p>}>
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain("datos");
  });

  it("`tieneDatos` manda sobre la heurística de `data`", () => {
    // Las páginas filtran y paginan antes de pintar, así que «hay datos» no es
    // siempre «data no está vacío»: la watchlist, por ejemplo, decide con
    // `visible.length`.
    const html = pintar(
      <QueryState
        query={{ data: [1, 2, 3] }}
        tieneDatos={false}
        vacio={<p>nada visible tras filtrar</p>}
      >
        <p>datos</p>
      </QueryState>,
    );
    expect(html).toContain("nada visible tras filtrar");
  });

  it("el error gana al vacío aunque `data` venga vacío", () => {
    // El caso real: react-query deja `data` en undefined al fallar, y la página
    // calcula `?? []`. Si el orden fuera el otro, volvería el defecto.
    const html = pintar(
      <QueryState
        query={{ isError: true, error: new Error("x"), data: [] }}
        tieneDatos={false}
        vacio={<p>No active filters</p>}
      >
        <p>datos</p>
      </QueryState>,
    );
    expect(html).not.toContain("No active filters");
    expect(html).toContain("No se pudo cargar");
  });
});

describe("Onboarding no continúa si el perfil no cargó (A-27)", () => {
  // El caso con consecuencia real: `useProfile` lleva `retry: false`, así que un
  // fallo dejaba `profile` en undefined, la página se pintaba NORMAL con
  // `skills: []` y `locations: []`, y «Find matches» hacía un PUT con esos
  // valores — destructivo sobre el perfil real.

  const estado = vi.hoisted(() => ({ perfil: undefined }));

  it("pinta la razón y NO el formulario", async () => {
    vi.doMock("../../hooks/useProfile", () => ({
      useProfile: () => estado.perfil,
      useUpdateProfile: () => ({ mutateAsync: async () => {}, isError: false }),
      useUploadCV: () => ({ mutateAsync: async () => ({}), isError: false }),
    }));
    vi.doMock("../../hooks/useCvAnalysis", () => ({
      useCvAnalysis: () => ({ start: () => {}, progress: null }),
    }));

    estado.perfil = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("500"),
    };

    const { default: OnboardingPage } = await import("../../pages/OnboardingPage");
    const html = pintar(<OnboardingPage />);

    expect(html).toContain("No se pudo cargar tu perfil");
    expect(html).toContain("sobrescribiría tu perfil");
    expect(html).not.toContain("Find matches");
  });
});

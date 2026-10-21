import { describe, expect, it, beforeEach } from "vitest";
import { useSearchStore } from "../stores/searchStore";

// DT-148 A18-01. El modo de fallo era este: la interfaz ofrecía nueve
// controles —cantón, idioma, seniority, contrato, rango de salario y tres
// órdenes— que la ruta canónica rechaza con 501, así que un usuario que
// filtraba por cantón recibía un error en vez de resultados. Lo que esta
// prueba impide es que vuelvan: el store es la única fuente de los
// parámetros de búsqueda, y si alguien le añade un campo que el catálogo no
// sirve, esto se pone rojo antes de que llegue a la interfaz.
const SERVIDOS = ["q", "source", "location", "remoteOnly"];

describe("los filtros de búsqueda que el store modela", () => {
  beforeEach(() => useSearchStore.getState().resetFilters());

  it("son EXACTAMENTE los que el catálogo del núcleo sirve", () => {
    const campos = Object.keys(useSearchStore.getState()).filter(
      (k) => typeof useSearchStore.getState()[k] !== "function",
    );
    expect(campos.sort()).toEqual([...SERVIDOS, "filtersOpen"].sort());
  });

  it("no vuelve a modelar ninguno de los que daban 501", () => {
    const estado = useSearchStore.getState();
    for (const muerto of [
      "canton",
      "language",
      "seniority",
      "contractType",
      "salaryMin",
      "salaryMax",
      "sort",
    ]) {
      expect(estado).not.toHaveProperty(muerto);
      expect(estado).not.toHaveProperty(
        `set${muerto[0].toUpperCase()}${muerto.slice(1)}`,
      );
    }
  });

  it("resetFilters devuelve location a vacío como el resto", () => {
    useSearchStore.getState().setLocation("Zürich");
    expect(useSearchStore.getState().location).toBe("Zürich");
    useSearchStore.getState().resetFilters();
    expect(useSearchStore.getState().location).toBe("");
  });
});

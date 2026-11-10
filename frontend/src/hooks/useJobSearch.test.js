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

describe("los chips reflejan los filtros que el store REALMENTE modela (A-36)", () => {
  // `useActiveFilterChips` leía SEIS campos que el store no modela —`canton`,
  // `language`, `seniority`, `contractType`, `salaryMin`, `salaryMax`— con sus
  // seis setters. El test de arriba de este fichero YA fijaba que el store
  // modela otros cuatro, así que el propio repo probaba que esos chips no
  // podían activarse nunca: 40 líneas muertas de DT-148 A18-01.
  //
  // Y lo visible no era sólo código muerto: `location` NO tenía chip, y es el
  // filtro que A18-01 puso como sustituto de los cuatro retirados — el único
  // que el núcleo aplica sobre el 100 % de las canónicas (el `canton` está en
  // el 8,5 %, medido).

  it("cada campo del store tiene su chip, y ninguno sobra", async () => {
    const { useSearchStore } = await import("../stores/searchStore");
    const modelados = Object.keys(useSearchStore.getState()).filter(
      (k) => typeof useSearchStore.getState()[k] !== "function",
    );

    const fuente = await import("../pages/SearchPage.jsx?raw").catch(() => null);
    // Si el import `?raw` no está disponible se lee del disco.
    const texto =
      fuente?.default ??
      (await import("node:fs")).readFileSync(
        new URL("../pages/SearchPage.jsx", import.meta.url),
        "utf8",
      );

    const cuerpo = texto.slice(
      texto.indexOf("function useActiveFilterChips"),
      texto.indexOf("return chips;"),
    );

    // Los campos que el store NO modela no pueden aparecer en los chips.
    for (const muerto of [
      "canton",
      "language",
      "seniority",
      "contractType",
      "salaryMin",
      "salaryMax",
    ]) {
      expect(modelados).not.toContain(muerto);
      expect(cuerpo).not.toContain(`s.${muerto}`);
    }

    // Y `location`, que es el que faltaba, sí.
    expect(modelados).toContain("location");
    expect(cuerpo).toContain("s.location");
  });
});

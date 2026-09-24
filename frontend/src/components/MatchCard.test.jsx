/**
 * H12 y M16 (T7) — dos defectos que se veían en pantalla.
 *
 * 1. «Translated from » sin idioma. La etiqueta sólo comprobaba
 *    `job_title_en`, así que con `job_language` nulo o vacío —que es el 96,9 %
 *    del feed mientras el almacén derivado no lo resuelve— se leía la frase a
 *    medias, con el hueco donde iba el idioma.
 * 2. Tres keywords de categoría escritas como comodines (`springer.*heim`) se
 *    comparaban con `includes`, que es literal: ningún título contiene los
 *    caracteres `.*`. Comprobado contra el corpus real: como literal casaban
 *    0 ofertas; como expresión regular, 1.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import MatchCard from "./MatchCard";
import { classifyMatch } from "../utils/jobCategories";

function pintar(match) {
  return renderToStaticMarkup(
    <MemoryRouter>
      <MatchCard match={match} />
    </MemoryRouter>,
  );
}

const BASE = {
  job_hash: "h1",
  job_title: "Entwickler",
  job_title_en: "Developer",
  job_company: "Empresa",
  job_location: "Zürich",
  job_url: "https://ejemplo.test/oferta",
  job_source: "arbeitnow",
  job_description: "Descripción corta.",
  score_final: 80,
  application_status: "detected",
  matching_skills: [],
  missing_skills: [],
  explanation: null,
  feedback: null,
  urgency_score: 0,
};

describe("la etiqueta de traducción", () => {
  it.each([null, "", undefined])(
    "no se pinta a medias con job_language = %s",
    (idioma) => {
      const html = pintar({ ...BASE, job_language: idioma });
      expect(html).not.toContain("Translated from");
    },
  );

  it("se pinta entera cuando hay idioma", () => {
    const html = pintar({ ...BASE, job_language: "de" });
    expect(html).toContain("Translated from");
    expect(html).toContain("DE");
  });
});

describe("clasificación por keywords", () => {
  it("una keyword con comodín casa como expresión regular", () => {
    const antes = classifyMatch({ job_title: "Springer Pflegeheim Zürich" });
    expect(antes).not.toBe("otros");
  });

  it("y NO casa el literal '.*', que es lo que se comparaba antes", () => {
    expect(classifyMatch({ job_title: "springer.*heim" })).toBe(
      classifyMatch({ job_title: "springer pflegeheim" }),
    );
  });

  it("las keywords normales siguen siendo literales", () => {
    expect(classifyMatch({ job_title: "Zzzz puesto inventado sin categoría" })).toBe(
      "otros",
    );
  });
});

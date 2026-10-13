/**
 * H12 y M16 (T7) — dos defectos que se veían en pantalla.
 *
 * 1. «Translated from » sin idioma. La etiqueta sólo comprobaba
 *    `job_title_en`, así que con `job_language` nulo o vacío —que es el 96,9 %
 *    del feed mientras el almacén derivado no lo resuelve— se leía la frase a
 *    medias, con el hueco donde iba el idioma.
 * 2. La categoría calculada por Core debe viajar hasta el navegador sin una
 *    segunda taxonomía JavaScript que pueda divergir de las facetas.
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

describe("clasificación canónica", () => {
  it("usa la categoría servida aunque el título sugiera otra", () => {
    expect(classifyMatch({ job_title: "Teacher", job_category: "A" })).toBe("A");
  });

  it("degrada categorías ausentes o desconocidas a otros", () => {
    expect(classifyMatch({ job_title: "Teacher" })).toBe("otros");
    expect(classifyMatch({ job_category: "Z" })).toBe("otros");
  });
});

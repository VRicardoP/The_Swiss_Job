/**
 * DT-148 A15 — la letra de la taxonomía no es un prefijo para todo.
 *
 * La pestaña pintaba `cat.id` delante de la etiqueta siempre que fuera
 * distinto de ella, y los items SINTÉTICOS de la pantalla principal y de
 * Guardados no tienen letra: se leía «__watchlist Watchlist 5» y
 * «otros Other 12». Sólo las categorías de la taxonomía (A..M) llevan letra.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import CategoryTabs from "./CategoryTabs";

function pintar(categories) {
  return renderToStaticMarkup(
    <CategoryTabs categories={categories} activeId={null} onChange={() => {}} />,
  );
}

describe("CategoryTabs", () => {
  it("no pinta el id de los items sintéticos", () => {
    const html = pintar([
      { id: "__watchlist", label: "Watchlist", count: 5 },
      { id: "otros", label: "Other", count: 12 },
    ]);

    expect(html).not.toContain("__watchlist<");
    expect(html).not.toContain(">__watchlist");
    expect(html).not.toContain(">otros<");
    expect(html).toContain("Watchlist");
    expect(html).toContain("Other");
  });

  it("sigue pintando la letra de la taxonomía", () => {
    const html = pintar([{ id: "H", label: "Docencia & Educación", count: 3 }]);

    expect(html).toContain(">H<");
    expect(html).toContain("Docencia");
  });
});

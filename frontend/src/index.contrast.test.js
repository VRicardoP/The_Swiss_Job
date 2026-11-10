import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * A-39 — contraste por debajo de AA en dos pares de tokens.
 *
 * Se CALCULA aquí en vez de fiarse de un valor escrito, porque la propuesta de
 * la auditoría (#8A8A93) daba 3,42:1 y habría dejado el defecto dentro con la
 * sensación de arreglado. Un token de color es exactamente la clase de cosa que
 * alguien ajusta «un poco» más adelante sin recalcular nada.
 */

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");

function token(nombre) {
  const m = css.match(new RegExp(`--color-${nombre}:\\s*(#[0-9A-Fa-f]{6})`));
  if (!m) throw new Error(`token no encontrado: --color-${nombre}`);
  return m[1];
}

// Luminancia relativa, WCAG 2.1 §relative luminance.
function luminancia(hex) {
  const canales = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = canales.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contraste(a, b) {
  const [claro, oscuro] = [luminancia(a), luminancia(b)].sort((x, y) => y - x);
  return (claro + 0.05) / (oscuro + 0.05);
}

const AA_TEXTO_NORMAL = 4.5;

describe("contraste de los tokens de texto (AA)", () => {
  it("quaternary sobre surface: era 2,56:1, el placeholder de la búsqueda principal", () => {
    expect(contraste(token("text-quaternary"), token("surface"))).toBeGreaterThanOrEqual(
      AA_TEXTO_NORMAL,
    );
  });

  it("secondary sobre surface-tertiary: los contadores de las pestañas", () => {
    // `tertiary` daba 4,40:1 ahí —justo por debajo— y a `text-[10px]`. Los
    // contadores pasaron a `secondary`, que da 7,03:1.
    expect(
      contraste(token("text-secondary"), token("surface-tertiary")),
    ).toBeGreaterThanOrEqual(AA_TEXTO_NORMAL);
  });

  it("tertiary sobre surface sigue pasando, que es su uso mayoritario", () => {
    expect(contraste(token("text-tertiary"), token("surface"))).toBeGreaterThanOrEqual(
      AA_TEXTO_NORMAL,
    );
  });

  it("primary y secondary sobre surface, por si alguien los aclara", () => {
    for (const t of ["text-primary", "text-secondary"]) {
      expect(contraste(token(t), token("surface"))).toBeGreaterThanOrEqual(
        AA_TEXTO_NORMAL,
      );
    }
  });
});

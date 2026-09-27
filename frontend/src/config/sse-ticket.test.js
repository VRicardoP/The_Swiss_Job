/**
 * H13/T10 — ningún consumidor del SSE puede volver a poner el token en la URL.
 *
 * T10 cambió el stream para que exija un vale de un solo uso: el endpoint
 * declara `ticket` como parámetro OBLIGATORIO y ya no conoce `token`. Pero la
 * migración se hizo consumidor a consumidor, y `useCvAnalysis` se quedó fuera:
 * siguió abriendo `?token=…` durante días, el backend respondía **422**, y la
 * barra de progreso del análisis de CV no avanzaba nunca. No saltó ninguna
 * prueba porque nadie comprobaba la FORMA de la URL, sólo el endpoint.
 *
 * Esta prueba mira el código fuente, no el comportamiento, a propósito: el
 * fallo estaba en cómo se construye una cadena, y eso no lo cubre ningún
 * mock del servidor.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function ficherosDeFuente(dir) {
  return readdirSync(dir).flatMap((entrada) => {
    const ruta = join(dir, entrada);
    if (statSync(ruta).isDirectory()) return ficherosDeFuente(ruta);
    return /\.(js|jsx)$/.test(entrada) && !/\.test\./.test(entrada) ? [ruta] : [];
  });
}

describe("el token de acceso no viaja en la URL del SSE", () => {
  const fuentes = ficherosDeFuente("src");

  it("encuentra ficheros que analizar (control de la propia prueba)", () => {
    expect(fuentes.length).toBeGreaterThan(20);
  });

  it("ningún fichero construye la URL del stream con el token", () => {
    const culpables = fuentes.filter((f) => {
      const texto = readFileSync(f, "utf8");
      // Sólo líneas de código: un comentario que MENCIONE el fallo es legítimo.
      return texto
        .split("\n")
        .filter((l) => !l.trim().startsWith("//") && !l.trim().startsWith("*"))
        .some((l) => /notifications\/stream\?[^`'"]*token=/.test(l));
    });
    expect(culpables).toEqual([]);
  });

  it("quien abre el stream lo hace con un vale", () => {
    const conStream = fuentes.filter((f) =>
      readFileSync(f, "utf8").includes("notifications/stream?"),
    );
    expect(conStream.length).toBeGreaterThan(0);
    for (const f of conStream) {
      expect(readFileSync(f, "utf8")).toMatch(/notifications\/stream\?ticket=/);
    }
  });
});

// Concatenador ligero de clases — evita añadir dependencias.
// Acepta strings, arrays, objetos { clase: condición } y valores falsy.
export function cn(...inputs) {
  const out = [];
  for (const value of inputs) {
    if (!value) continue;
    if (typeof value === "string") {
      out.push(value);
    } else if (Array.isArray(value)) {
      const nested = cn(...value);
      if (nested) out.push(nested);
    } else if (typeof value === "object") {
      for (const [key, cond] of Object.entries(value)) {
        if (cond) out.push(key);
      }
    }
  }
  return out.join(" ");
}

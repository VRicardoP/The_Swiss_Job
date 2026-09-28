import DOMPurify from "dompurify";

// Whitelist conservadora para descripciones de jobs y borradores generados
// por LLM. Permitimos formato básico pero NO scripts, event handlers,
// objetos embebidos ni atributos peligrosos (style, on*).
const DEFAULT_ALLOWED_TAGS = [
  "p", "br", "strong", "em", "b", "i", "u",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "ul", "ol", "li",
  "blockquote", "code", "pre",
  "a", "span", "div",
];

const DEFAULT_ALLOWED_ATTR = ["href", "title", "target", "rel", "class"];

// A20-18: el comentario prometía «forzar rel=noopener» y no había código que
// lo hiciera. Un enlace con target=_blank sin rel deja `window.opener` al
// destino. El hook es global de DOMPurify; se registra una sola vez.
if (typeof DOMPurify.addHook === "function") {
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A" && node.getAttribute("target") === "_blank") {
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

export function sanitizeHtml(dirty, options = {}) {
  if (!dirty || typeof dirty !== "string") return "";
  const config = {
    ALLOWED_TAGS: options.allowedTags ?? DEFAULT_ALLOWED_TAGS,
    ALLOWED_ATTR: options.allowedAttr ?? DEFAULT_ALLOWED_ATTR,
    // `target` sobrevive; el hook de arriba le añade rel=noopener noreferrer.
    ADD_ATTR: ["target"],
    KEEP_CONTENT: true,
  };
  return DOMPurify.sanitize(dirty, config);
}

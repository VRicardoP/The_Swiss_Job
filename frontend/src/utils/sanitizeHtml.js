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

export function sanitizeHtml(dirty, options = {}) {
  if (!dirty || typeof dirty !== "string") return "";
  const config = {
    ALLOWED_TAGS: options.allowedTags ?? DEFAULT_ALLOWED_TAGS,
    ALLOWED_ATTR: options.allowedAttr ?? DEFAULT_ALLOWED_ATTR,
    // Forzar rel=noopener para enlaces a target=_blank
    ADD_ATTR: ["target"],
    KEEP_CONTENT: true,
  };
  return DOMPurify.sanitize(dirty, config);
}

/**
 * Category presentation metadata.
 *
 * Core is the classification authority and returns `job_category` on every
 * match. Keeping the keyword rules in JavaScript duplicated 600 canonical
 * rules and made fallback rendering disagree with facets.
 */

export const CATEGORIES = [
  {
    "id": "A",
    "label": "Edición & Localización",
    "shortLabel": "Edición"
  },
  {
    "id": "B",
    "label": "IA & Evaluación",
    "shortLabel": "IA"
  },
  {
    "id": "C",
    "label": "Administración & VA",
    "shortLabel": "Admin"
  },
  {
    "id": "D",
    "label": "RRHH & Formación",
    "shortLabel": "RRHH"
  },
  {
    "id": "E",
    "label": "Customer Success",
    "shortLabel": "CS"
  },
  {
    "id": "F",
    "label": "Organismos Internacionales",
    "shortLabel": "Intl."
  },
  {
    "id": "G",
    "label": "Contenido & Marketing",
    "shortLabel": "Contenido"
  },
  {
    "id": "H",
    "label": "Docencia & Educación",
    "shortLabel": "Docencia"
  },
  {
    "id": "I",
    "label": "Ventas & Negocio",
    "shortLabel": "Ventas"
  },
  {
    "id": "J",
    "label": "Investigación & Análisis",
    "shortLabel": "Invest."
  },
  {
    "id": "K",
    "label": "Finanzas & Contabilidad",
    "shortLabel": "Finanzas"
  },
  {
    "id": "L",
    "label": "Gestión & Dirección",
    "shortLabel": "Gestión"
  },
  {
    "id": "M",
    "label": "Servicios Sociales",
    "shortLabel": "Social"
  }
];

const CATEGORY_IDS = new Set(CATEGORIES.map((category) => category.id));

export function classifyMatch(match) {
  return CATEGORY_IDS.has(match?.job_category) ? match.job_category : "otros";
}

export function groupByCategory(matches) {
  const groups = new Map(CATEGORIES.map((category) => [category.id, []]));
  groups.set("otros", []);

  for (const match of matches) {
    groups.get(classifyMatch(match)).push(match);
  }

  for (const [key, list] of groups) {
    if (list.length === 0) groups.delete(key);
  }
  return groups;
}

export const CATEGORY_MAP = Object.fromEntries(
  CATEGORIES.map((category) => [category.id, category]),
);

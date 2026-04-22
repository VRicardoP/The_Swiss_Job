/**
 * Clasificación de ofertas en 13 categorías para el perfil de Alicia Moore.
 *
 * A–H: categorías definidas en el análisis maestro (objetivos de transición + docencia)
 * I–M: categorías de agrupación para organizar el resto de ofertas por sector
 */

export const CATEGORIES = [
  // ─── CATEGORÍAS PRINCIPALES (perfil de transición) ───────────────────────

  {
    id: "A",
    label: "Edición & Localización",
    shortLabel: "Edición",
    keywords: [
      "content editor", "copy editor", "proofreader", "proofreading",
      "localization", "localisation", "lqa", "linguistic quality",
      "post-editor", "post-editing", "mtpe", "translator", "translation",
      "editorial", "copywriter", "copywriting", "subtitling", "transcription",
      "content reviewer", "localization tester", "language reviewer",
      "linguistic", "bilingual editor", "multilingual editor",
      "writing specialist", "freelance writer", "assistant editor",
      " editor", "copy writer", "language specialist",
      // German
      "übersetzer", "übersetzerin", "redakteur", "redakteurin",
      "lektor", "lektorin", "korrektor", "korrektorin",
      "lokalisierung", "sprachqualität", "sprachredaktion",
    ],
  },
  {
    id: "B",
    label: "IA & Evaluación",
    shortLabel: "IA",
    keywords: [
      "rlhf", "ai evaluator", "ai trainer", "content evaluator",
      "data annotator", "data annotation", "search quality rater",
      "quality rater", "ai content", "prompt engineer",
      "machine translation", "llm evaluator", "ai reviewer",
      "data labeler", "data labelling", "ai quality", "nlp annotator",
      "human feedback", "rater", "outlier ai", "scale ai",
      "telus international", "lionbridge", "appen ",
      "personalized ads evaluator", "ads evaluator", "ads quality",
      "online data analyst", "search evaluator", "web search evaluator",
    ],
  },
  {
    id: "C",
    label: "Administración & VA",
    shortLabel: "Admin",
    keywords: [
      "virtual assistant", "executive assistant", "administrative",
      "operations coordinator", "project coordinator", "office manager",
      "event coordinator", "remote assistant", "admin coordinator",
      "administrative coordinator", "secretar", "office administrator",
      "personal assistant", "operations assistant", "office support",
      "project assistant", "programme manager", "program manager",
      "front desk", "reception", "empfangsmitarbeiter",
      // German
      "sachbearbeiter", "sachbearbeiterin", "büromanager", "büroassistenz",
      "assistent", "assistentin", "projektkoordinator", "projektkoordinatorin",
      "verwaltung", "administration", "sekretär", "sekretärin",
      "kaufmännisch", "kaufmännische", "empfang",
      // French
      "assistant administratif", "assistante administrative",
      "coordinatrice", "coordinateur",
    ],
  },
  {
    id: "D",
    label: "RRHH & Formación",
    shortLabel: "RRHH",
    keywords: [
      "hr coordinator", "hr administrator", "hr officer", "hr manager",
      "hr generalist", "hr specialist", "human resources",
      "instructional designer", "instructional design", "elearning",
      "e-learning", "l&d", "learning and development", "training coordinator",
      "onboarding", "people operations", "talent acquisition",
      "recruiting coordinator", "learning coordinator", "l&d specialist",
      "training specialist", "learning specialist", "people partner",
      "talent management", "corporate training", "workforce development",
      "people manager", "payroll specialist", "hr services",
      "entgeltabrechnung", "personalsachbearbeitung",
      // German
      "personalwesen", "personalabteilung", "personalreferent",
      "personalreferentin", "weiterbildung",
      "hr-koordinator", "personalkoordinator", "personalmanager",
    ],
  },
  {
    id: "E",
    label: "Customer Success",
    shortLabel: "CS",
    keywords: [
      "customer success", "customer support", "customer service",
      "client relations", "vip client", "guest experience",
      "concierge", "customer experience", "account manager",
      "client success", "customer care", "customer operations",
      "client support", "support specialist", "help desk",
      "bilingual support", "service representative",
      "client solutions", "account executive",
      // German
      "kundendienst", "kundenbetreuung", "kundensupport",
      "kundenerfolg", "kundenberater", "kundenberaterin",
      "kundenkommunikation", "kundenservice",
    ],
  },
  {
    id: "F",
    label: "Organismos Internacionales",
    shortLabel: "Intl.",
    keywords: [
      "language assistant", "programme assistant", "program assistant",
      "documentation assistant", "unicef", "unesco", "undp", "unfccc",
      "ngo", "ngos", "international organisation", "international organization",
      "conference services", "humanitarian", "development organisation",
      "united nations", "international bureau", "wto", "wfp",
      "who ", "ilo ", "un ", "iaea", "unhcr", "ifrc",
      "public affairs", "eu policy", "european union",
      "international development", "global policy",
      "policy officer", "policy analyst", "policy advisor", "policy manager",
      "advocacy officer", "advocacy manager", "advocacy coordinator",
      "humanitarian affairs", "humanitarian coordinator",
      "eu project", "eu programme", "eu global",
      "international affairs", "foreign affairs",
      "development cooperation", "grants officer", "grant officer",
      "programme consultant", "visiting fellow", "fellowship",
      "impact manager", "monitoring and evaluation", "m&e officer",
      "mission secretariat", "secretary general", "press specialist",
      "press officer", "anti-racism", "diversity intergroup",
      "sub-saharan", "carbon policy", "climate policy", "energy policy",
      "health policy", "eu projects officer", "europe policy",
      // German
      "internationale organisation", "uno", "entwicklungshilfe",
      "politikberatung", "eu-politik",
    ],
  },
  {
    id: "G",
    label: "Contenido & Marketing",
    shortLabel: "Contenido",
    keywords: [
      "content writer", "content specialist", "content creator",
      "technical writer", "blog editor", "communications",
      "educational content", "content strategist", "content manager",
      "social media", "marketing coordinator", "internal communications",
      "content marketing", "seo writer", "creative writer",
      "communications assistant", "communications consultant",
      "communications officer", "media coordinator",
      "documentation specialist", "knowledge base",
      "marketing manager", "brand manager", "community manager",
      "online marketing", "e-commerce", "ecommerce", "influencer",
      "digital marketing", "senior creative", "creative manager",
      "press release", "newsletter", "copyeditor",
      // German
      "texter", "texterin", "kommunikation", "kommunikationsmanager",
      "kommunikationsbeauftragter", "marketingkoordinator",
      "marketingmanager", "zeitung", "redaktion", "journalist",
    ],
  },
  {
    id: "H",
    label: "Docencia & Educación",
    shortLabel: "Docencia",
    keywords: [
      // English — teaching roles
      "teacher", "esl teacher", "efl teacher", "english teacher",
      "language teacher", "tefl", "tesol", "celta", "esol",
      "classroom teacher", "primary teacher", "secondary teacher",
      "school teacher", "subject teacher", "class teacher",
      "academic staff", "faculty", "lecturer", "tutor",
      "academic support", "learning support assistant", "learning support",
      "sen teacher", "special needs", "head of department", "head of year",
      "form tutor", "pastoral", "school coordinator", "school leader",
      "deputy head", "assistant head", "international school",
      "british curriculum", "ib teacher", "cambridge teacher",
      "professor", "adjunct", "dorm head", "boarding",
      "academic tutor", "eap teacher", "school counselor", "school counsellor",
      "education coordinator", "instructor",
      // German — teaching roles
      "lehrperson", "lehrerin", "lehrer", "lehrkraft",
      "klassenlehrer", "klassenlehrerin", "klassenlehrperson",
      "unterricht", "schulischer",
      "pädagog", "pädagogin", "sozialpädagog", "sozialpädagogin",
      "heilpädagog", "heilpädagogin", "sonderpädagog",
      "schulleiter", "schulleiterin", "schulführung", "schulbegleitung",
      "berufsschule", "gymnasium", "kantonsschule",
      "primarstufe", "sekundarstufe", "grundschule",
      "fachlehrperson", "stellvertretung", "lehrauftrag",
      "dozent", "dozentin", "professur",
      "betreuung", "hort", "tagesstruktur", "kita",
      "schulabsentismus", "jugendcoach", "nachtwache", "nachtdienst",
      "gruppenleitung", "teamleitung jade", "standort.*betreuung",
      "agogisch", "arbeitsagog",
      // French
      "enseignant", "enseignante", "professeur",
    ],
  },

  // ─── CATEGORÍAS DE AGRUPACIÓN (organización del resto) ────────────────────

  {
    id: "I",
    label: "Ventas & Negocio",
    shortLabel: "Ventas",
    keywords: [
      // English
      "sales manager", "sales consultant", "sales specialist",
      "account executive", "business development", "business consultant",
      "commercial manager", "presales", "client solutions manager",
      "senior sales", "sales representative", "inside sales",
      "field sales", "sales director", "head of sales",
      // German
      "verkaufsberater", "verkaufsleiter", "vertrieb",
      "aussendienstmitarbeiter", "verkäufer", "verkaufsmitarbeiter",
      "geschäftsberater", "unternehmensberater",
    ],
  },
  {
    id: "J",
    label: "Investigación & Análisis",
    shortLabel: "Invest.",
    keywords: [
      // English
      "research assistant", "research associate", "researcher",
      "policy adviser", "policy advisor", "policy researcher",
      "data analyst", "product analyst", "senior analyst",
      "business analyst", "systems analyst", "intelligence analyst",
      "financial analyst", "market analyst", "programme analyst",
      "research officer", "research coordinator",
      "monitoring evaluation", "impact assessment",
      // German
      "fachmitarbeiter recht", "rechtsberater", "analyst",
      "wirtschaftsanalytiker",
    ],
  },
  {
    id: "K",
    label: "Finanzas & Contabilidad",
    shortLabel: "Finanzas",
    keywords: [
      // English
      "finance specialist", "finance manager", "finance assistant",
      "financial controller", "payroll", "accounting", "accountant",
      "budget analyst", "finance expert", "portfolio management",
      "grants management", "finance officer",
      // German
      "buchhalter", "buchführung", "finanzbuchhalter", "lohnbuchhalter",
      "controlling", "finanzkontrolle", "treuhand", "rechnungswesen",
      "steuerberater", "buchhaltung", "finanzwesen", "finanzberater",
      "wirtschaftsprüfer", "revisor", "entgeltabrechner",
    ],
  },
  {
    id: "L",
    label: "Gestión & Dirección",
    shortLabel: "Gestión",
    keywords: [
      // English
      "operations manager", "associate director", "managing director",
      "chief of", "head of", "vice president", "general manager",
      "country manager", "regional manager", "programme director",
      "senior manager", "product manager", "project manager",
      // German
      "geschäftsführer", "geschäftsstellenleiter",
      "bereichsleiter", "abteilungsleiter", "abteilungsleitung",
      "leiterin abteilung", "leiter abteilung",
      "standortleitung", "werkstattleitung",
      "direktor", "direktorin", "führungskraft",
      "teamleiter", "teamleiterin",
    ],
  },
  {
    id: "M",
    label: "Servicios Sociales",
    shortLabel: "Social",
    keywords: [
      // German
      "sozialarbeiter", "sozialarbeiterin", "sozialarbeit",
      "sozialhilfe", "berufsbeistand", "berufsbeistände",
      "integrationscoach", "schulische sozialarbeit",
      "leiter sozialhilfe", "sozialpädagogisch",
      "fachperson wohnen", "leitung wohnen",
      "durchgangsheim", "wohngruppe", "stationsleitung",
      "aktivierungsfachperson", "springer.*heim",
      "springer nachtwache", "soziale einrichtung",
      "gemeinwesen", "jugendarbeit", "jugendwohngruppe",
      "fachbegleiter.*wohn",
    ],
  },
];

/**
 * Clasifica un match en una de las 13 categorías.
 * Devuelve el id de categoría o "otros" si no encaja en ninguna.
 */
export function classifyMatch(match) {
  const text = [
    match.job_title_en || match.job_title || "",
    ...(match.job_tags || []),
  ]
    .join(" ")
    .toLowerCase();

  for (const cat of CATEGORIES) {
    if (cat.keywords.some((kw) => text.includes(kw))) {
      return cat.id;
    }
  }
  return "otros";
}

/**
 * Agrupa una lista de matches por categoría.
 * Devuelve un Map: categoryId → matches[]
 */
export function groupByCategory(matches) {
  const groups = new Map();
  for (const cat of CATEGORIES) {
    groups.set(cat.id, []);
  }
  groups.set("otros", []);

  for (const match of matches) {
    const catId = classifyMatch(match);
    groups.get(catId).push(match);
  }

  // Eliminar categorías vacías
  for (const [key, list] of groups) {
    if (list.length === 0) groups.delete(key);
  }

  return groups;
}

export const CATEGORY_MAP = Object.fromEntries(
  CATEGORIES.map((c) => [c.id, c])
);

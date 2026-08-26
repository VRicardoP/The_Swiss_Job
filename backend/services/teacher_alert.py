"""Detección de ofertas de PROFESOR DE PRIMARIA en colegios suizos.

Reutiliza la categoría del JobClassifier (H = docencia, ya asignada en ingesta
con keywords DE/FR/IT/EN) y la restringe al NIVEL primaria con marcadores
multiidioma. Pensado para el aviso por email a TEACHER_ALERT_EMAIL.

NOTA: el objetivo general del proyecto es SALIR de la docencia (categoría H
penalizada en el scoring); esta alerta es un opt-in aparte y no toca ese flujo.
"""

from html import escape as _esc

# Categoría de docencia que asigna JobClassifier (ver services/job_classifier.py).
_TEACHING_CATEGORY = "H"

# Marcadores de NIVEL PRIMARIA en DE/FR/IT/EN/ES. Se comparan como substring en
# minúsculas sobre título + tags. Frases específicas para evitar falsos positivos.
_PRIMARY_MARKERS: tuple[str, ...] = (
    # EN
    "primary teacher",
    "primary school",
    "primary years",
    "primary class",
    # DE
    "primarlehr",
    "primarstufe",
    "primarschule",
    "grundschule",
    "grundstufe",
    # FR
    "enseignant primaire",
    "enseignante primaire",
    "école primaire",
    "degré primaire",
    "cycle primaire",
    "instituteur",
    "institutrice",
    # IT
    "scuola primaria",
    "scuola elementare",
    "maestro elementare",
    "insegnante scuola primaria",
    # ES
    "escuela primaria",
    "profesor de primaria",
    "maestro de primaria",
)

# G4/P1-4 — marcadores COMPUESTOS: todos sus términos deben aparecer en el
# haystack, en cualquier orden y a cualquier distancia. Existen por la forma
# EPICENA del francés administrativo suizo —«Enseignant-e», «Enseignant·e»,
# «Enseignant(e)», «Enseignants-es»—, que es la convención DOMINANTE en la
# Suiza francófona (Champittet/NAE, Ecolint, HautLac, ISCS) y no casa con
# ningún literal contiguo de la lista de arriba: entre «enseignant» y
# «primaire» se cuela el sufijo epiceno, así que «enseignant-e primaire» no
# contiene «enseignant primaire». Un par de términos lo cubre sin tener que
# enumerar cada grafía (guion, punto medio, paréntesis, barra, plural
# «-es»), y de paso recoge las inversiones («Un-e enseignant-e pour le degré
# primaire»). Sobre-inclusivo a propósito: esta alerta es opt-in y su razón
# de ser es NO perder la vacante.
_PRIMARY_MARKER_PAIRS: tuple[tuple[str, ...], ...] = (("enseignant", "primaire"),)


def is_primary_teacher_job(
    category: str | None, title: str | None, tags: list[str] | None
) -> bool:
    """True si el job es docencia (categoría H) Y de nivel primaria."""
    if (category or "") != _TEACHING_CATEGORY:
        return False
    haystack = (title or "").lower() + " " + " ".join(tags or []).lower()
    if any(marker in haystack for marker in _PRIMARY_MARKERS):
        return True
    return any(all(term in haystack for term in pair) for pair in _PRIMARY_MARKER_PAIRS)


def build_alert_email(jobs: list) -> tuple[str, str, str]:
    """Construye (subject, texto_plano, html) para un lote de ofertas.

    `jobs` son instancias de Job (o similares) con .title/.company/.canton/
    .location/.url. El HTML escapa el contenido para evitar inyección.
    """
    n = len(jobs)
    plural = "s" if n != 1 else ""
    subject = f"{n} nueva{plural} oferta{plural} de profesor de primaria (Suiza)"

    text_lines: list[str] = []
    html_items: list[str] = []
    for job in jobs:
        loc = job.canton or job.location or "—"
        company = job.company or "—"
        url = job.url or ""
        text_lines.append(f"- {job.title} · {company} · {loc}\n  {url}")
        html_items.append(
            f"<li><strong>{_esc(job.title or '')}</strong> — "
            f"{_esc(company)} ({_esc(loc)})<br>"
            f'<a href="{_esc(url)}">{_esc(url)}</a></li>'
        )

    intro = "Nuevas ofertas de profesor de primaria en colegios suizos:"
    text = intro + "\n\n" + "\n".join(text_lines)
    html = f"<p>{intro}</p><ul>{''.join(html_items)}</ul>"
    return subject, text, html

"""Construcción del email de digest diario de matches.

Función pura (sin BD ni SMTP): recibe las ofertas ya seleccionadas para un
usuario y devuelve (subject, texto_plano, html). El HTML escapa el contenido
para evitar inyección. La selección y el envío viven en tasks/digest_tasks.py.
"""

from html import escape as _esc


def build_digest_email(jobs: list[dict]) -> tuple[str, str, str]:
    """Construye (subject, texto, html) para el digest de un usuario.

    Cada elemento de `jobs` es un dict con: title, company, location, url, score.
    Se asume ya ordenado por score descendente y acotado por el llamante.
    """
    n = len(jobs)
    plural = "s" if n != 1 else ""
    subject = f"{n} nueva{plural} oferta{plural} recomendada{plural} para ti"

    text_lines: list[str] = []
    html_items: list[str] = []
    for job in jobs:
        title = job.get("title") or "—"
        company = job.get("company") or "—"
        loc = job.get("location") or "—"
        url = job.get("url") or ""
        score = job.get("score")
        score_str = f"{round(score)}%" if score is not None else "—"
        text_lines.append(f"- [{score_str}] {title} · {company} · {loc}\n  {url}")
        html_items.append(
            f"<li><strong>{_esc(title)}</strong> "
            f"<em>({_esc(score_str)} match)</em><br>"
            f"{_esc(company)} — {_esc(loc)}<br>"
            f'<a href="{_esc(url)}">{_esc(url)}</a></li>'
        )

    intro = "Tus mejores ofertas nuevas según el matching con tu perfil:"
    text = intro + "\n\n" + "\n".join(text_lines)
    html = f"<p>{intro}</p><ul>{''.join(html_items)}</ul>"
    return subject, text, html

"""A19-15 §A.1 — la descripción de arbeitnow llegaba en HTML crudo.

Medido el 2026-09-23 sobre producción: **111 de 111** ofertas de arbeitnow
mostraban `<p>`, `<ul>` y `<br>` en la tarjeta. No es un fallo del consumidor:
el productor legacy limpió siempre la descripción (`backend/providers/
arbeitnow.py:78`, `strip_html_tags`), y al portar la fuente al core en el
traspaso nativo del 22-09 se copió la SELECCIÓN de campos pero no esa llamada.

La paridad es con el productor que se retira, no con una idea nueva de lo que
debería ser un texto limpio: las dos `strip_html_tags` (backend/utils/text.py
y jobhunt_core/harvest/providers/rss_text.py) son la misma función, carácter a
carácter, y las entidades se dejan SIN decodificar a propósito — decodificarlas
cambiaría el texto embebido y movería todos los vectores de la fuente.
"""

from jobhunt_core.harvest.normalize import normalize_offer

# Forma real del campo `description` del feed de arbeitnow.
CRUDA = (
    "<p>Wir suchen eine <strong>Fachkraft</strong>.</p>\r\n"
    "<ul>\n<li>Erfahrung mit Python</li>\n<li>Deutsch &amp; Englisch</li>\n</ul>"
    "<br />\n<p>Bewerbung an&nbsp;uns.</p>"
)
OFERTA = {
    "slug": "fachkraft-zurich-123",
    "title": "Fachkraft",
    "company_name": "Beispiel AG",
    "description": CRUDA,
    "tags": ["python"],
    "location": "Zürich",
    "remote": False,
    "url": "https://www.arbeitnow.com/jobs/companies/x/fachkraft-123",
    "created_at": 1758000000,
}


def test_la_descripcion_llega_sin_etiquetas():
    content = normalize_offer("arbeitnow", OFERTA)
    texto = content["description"]

    assert "<" not in texto and ">" not in texto, texto
    assert texto == (
        "Wir suchen eine Fachkraft . Erfahrung mit Python Deutsch "
        "&amp; Englisch Bewerbung an&nbsp;uns."
    )


def test_las_entidades_se_conservan():
    """Control de la cota: NO se decodifican, igual que en el resto de fuentes
    nativas. Si algún día se decide decodificar, hay que hacerlo en TODAS a la
    vez y asumir que se re-embebe el corpus entero."""
    content = normalize_offer("arbeitnow", OFERTA)
    assert "&amp;" in content["description"]
    assert "&" in content["description"] and " & " not in content["description"]


def test_una_descripcion_ausente_sigue_siendo_ausencia():
    """El feed trae ofertas sin descripción. Limpiar no puede convertir una
    ausencia en una cadena vacía: son estados distintos y el consumidor
    distingue «no publica descripción» de «descripción en blanco». La coerción
    central de `normalize_offer` ya lo resuelve — se comprueba que sigue
    haciéndolo DESPUÉS de meter la limpieza por delante."""
    for valor in (None, "", "   ", "<p></p>", "<br/>"):
        assert (
            normalize_offer("arbeitnow", {**OFERTA, "description": valor})[
                "description"
            ]
            is None
        ), valor


def test_un_texto_ya_limpio_no_se_toca():
    """Paridad con el legacy también aquí: limpiar lo limpio es la identidad,
    salvo el colapso de espacios que las dos funciones hacen igual."""
    content = normalize_offer("arbeitnow", {**OFERTA, "description": "Texto llano."})
    assert content["description"] == "Texto llano."

"""A-47 — inyección de iCalendar desde un título del corpus cosechado.

`_ics_escape` escapaba `\\`, `;`, `,` y `\n`, pero NO el `\r`. El `SUMMARY` y el
`DESCRIPTION` del `.ics` llevan `job.title` y el nombre del colegio, que vienen
del corpus, y la normalización del núcleo sólo hace `.strip()`
(`harvest/normalize.py`): no toca los controles internos. Como las líneas del
fichero se unen con CRLF, un título con `\r\n` dentro cerraba la propiedad e
inyectaba propiedades o `VEVENT`s enteros en el calendario que el usuario
importa.

Control negativo: quitando las dos líneas del `\r` en `_ics_escape`, los dos
primeros casos fallan.
"""

from routers.watchlist import _ics_escape


class TestInyeccionPorRetornoDeCarro:
    def test_un_CRLF_no_puede_cerrar_la_propiedad(self):
        escapado = _ics_escape("Profesor\r\nBEGIN:VEVENT")
        assert "\r" not in escapado, "un \\r crudo cierra la línea del .ics"
        assert "\n" not in escapado, "ni un \\n crudo"
        # El salto se conserva como la secuencia que iCalendar sí define.
        assert escapado == "Profesor\\nBEGIN:VEVENT"

    def test_un_CR_suelto_tampoco(self):
        escapado = _ics_escape("Profesor\rinyectado")
        assert "\r" not in escapado
        assert escapado == "Profesorinyectado"

    def test_el_resultado_no_contiene_NINGUN_control_de_linea(self):
        """La propiedad que de verdad importa, dicha de una vez: después de
        escapar no puede quedar nada capaz de abrir una línea nueva."""
        titulo = "Lehrer\r\nEND:VEVENT\rBEGIN:VEVENT\nSUMMARY:falso"
        escapado = _ics_escape(titulo)
        assert not set(escapado) & {"\r", "\n"}


class TestLoQueYaEscapabaBien:
    """Contraparte: no haber roto lo que ya funcionaba."""

    def test_la_barra_invertida_se_duplica_y_va_PRIMERO(self):
        # Si el orden se invirtiera, los escapes que añaden las demás reglas se
        # volverían a escapar y el valor saldría corrupto.
        assert _ics_escape("\\") == "\\\\"
        assert _ics_escape("a;b") == "a\\;b"

    def test_punto_y_coma_y_coma(self):
        assert _ics_escape("Zurich, Suiza; remoto") == "Zurich\\, Suiza\\; remoto"

    def test_un_titulo_normal_no_se_toca(self):
        assert _ics_escape("Profesor de primaria") == "Profesor de primaria"

    def test_un_salto_de_linea_simple_sigue_escapandose(self):
        assert _ics_escape("linea1\nlinea2") == "linea1\\nlinea2"

"""A-66 — el guard buscaba UN literal quemado en vez de la FORMA.

Su docstring prometía «un `${VAR:-contraseña}` devuelve una clave conocida», y
la implementación era `CLAVES_QUEMADAS = ("swissjob_dev_2024",)` más un
`in texto`. Así que se le colaban dos credenciales por defecto en nueve sitios
del compose base —`CORE_REDIS_PASSWORD:-core_redis_dev` y
`CORE_DB_PASSWORD:-jobhunt_core_dev`, y la segunda es la contraseña con la que
`core-migrate` CREA el rol `jobhunt_core`—, mientras `CLAUDE.md` afirmaba que la
guarda garantizaba «ninguna contraseña publicada como defecto».

Es el mismo error de diseño que dejó pasar la credencial de A-02: **un guard que
busca valores concretos en vez de formas no es un guard.**
"""

from pathlib import Path

import pytest

from scripts.check_compose_exposure import (
    DEFAULTS_SENSIBLES_TOLERADOS,
    _revisa_claves_por_defecto,
)


def _compose(tmp_path: Path, contenido: str) -> str:
    fichero = tmp_path / "docker-compose.prueba.yml"
    fichero.write_text(contenido, encoding="utf-8")
    return str(fichero)


class TestDefaultsPorForma:
    @pytest.mark.parametrize(
        "clave",
        [
            "MI_PASSWORD",
            "ALGUN_SECRET",
            "UN_TOKEN_NUEVO",
            "OTRA_API_KEY",
            "NUEVO_DSN",
        ],
    )
    def test_cualquier_clave_sensible_con_default_falla(self, tmp_path, clave):
        """Lo que la lista de literales no podía: cazar la contraseña que nadie
        ha quemado todavía, que es justo la que importa."""
        fichero = _compose(tmp_path, f"x: ${{{clave}:-valor-inventado}}\n")
        fallos = _revisa_claves_por_defecto(fichero)
        assert any(clave in f for f in fallos), fallos

    def test_el_fallo_NO_vuelca_el_valor(self, tmp_path):
        """Reportar el valor metería la credencial en el log del CI, que es
        exactamente cómo se filtró la de A-02."""
        fichero = _compose(tmp_path, "x: ${MI_PASSWORD:-s3cr3to-que-no-debe-salir}\n")
        fallos = _revisa_claves_por_defecto(fichero)
        assert fallos
        assert not any("s3cr3to" in f for f in fallos), fallos

    def test_exigirla_con_VAR_interrogacion_NO_falla(self, tmp_path):
        """La forma correcta, que es la que el mensaje recomienda."""
        fichero = _compose(tmp_path, "x: ${MI_PASSWORD:?es obligatoria}\n")
        assert _revisa_claves_por_defecto(fichero) == []

    def test_una_variable_SIN_default_tampoco_falla(self, tmp_path):
        fichero = _compose(tmp_path, "x: ${MI_PASSWORD}\n")
        assert _revisa_claves_por_defecto(fichero) == []

    def test_una_clave_NO_sensible_con_default_no_falla(self, tmp_path):
        """`CORE_IMAGE_TAG:-dev` o `HOST_BIND_IP:-127.0.0.1` son defaults
        legítimos: el guard vigila credenciales, no configuración."""
        fichero = _compose(
            tmp_path, "a: ${CORE_IMAGE_TAG:-dev}\nb: ${HOST_BIND_IP:-127.0.0.1}\n"
        )
        assert _revisa_claves_por_defecto(fichero) == []

    def test_los_comentarios_no_cuentan(self, tmp_path):
        fichero = _compose(tmp_path, "# ejemplo: ${MI_PASSWORD:-algo}\n")
        assert _revisa_claves_por_defecto(fichero) == []


class TestElRatchetEsRatchetYnoAmnistia:
    """La tolerancia de los dos defaults que ya existían está congelada POR
    NOMBRE, y esa diferencia es el punto: una clave nueva falla."""

    def test_los_dos_tolerados_estan_escritos_explicitamente(self):
        assert DEFAULTS_SENSIBLES_TOLERADOS == frozenset(
            {
                ("docker-compose.yml", "CORE_DB_PASSWORD"),
                ("docker-compose.yml", "CORE_REDIS_PASSWORD"),
            }
        ), (
            "si esta lista crece, alguien ha tolerado un default nuevo en vez de "
            "exigirlo con ${VAR:?} — y eso es una decisión, no un detalle"
        )

    def test_la_tolerancia_va_por_FICHERO_y_no_solo_por_clave(self, tmp_path):
        """Un `CORE_DB_PASSWORD:-x` en OTRO compose sí debe fallar: lo tolerado
        es el default concreto que ya existía, no el nombre en cualquier sitio."""
        fichero = _compose(tmp_path, "x: ${CORE_DB_PASSWORD:-jobhunt_core_dev}\n")
        fallos = _revisa_claves_por_defecto(fichero)
        assert any("CORE_DB_PASSWORD" in f for f in fallos), fallos

    def test_el_compose_base_REAL_pasa(self):
        """Con los dos congelados, el compose en vigor queda limpio. Si esto
        falla, han aparecido defaults nuevos."""
        assert _revisa_claves_por_defecto("docker-compose.yml") == []

"""A-33 — el guard más crítico del repo no tenía un solo test.

`check_source_ownership.py` corre como **paso 1 del CI** y como **primera línea
de `scripts/deploy_nas.sh`**, y su `validate` tenía **CC 80**: la función más
compleja del repositorio con diferencia (la siguiente es 32). Ninguna cota la
amparaba — la A19-13 se quedó sin objeto al retirarse las herramientas de la
Fase E en P3 de DT-145.

Un guard de 80 ramas sin pruebas es un guard que nadie puede auditar: cada rama
nueva se añade por acumulación y no hay forma de saber si alguna quedó muerta.
El relevo §7 ya documenta una sorpresa de este mismo script («busca el literal
en TODO el fichero, comentarios incluidos»), que es exactamente lo que produce
una función así.

Estos tests son de CARACTERIZACIÓN: se escribieron ANTES de partir `validate`,
para que el refactor fuese verificable en vez de confiado. Fijan el mensaje de
cada familia de invariante, porque **el mensaje es el contrato**: es lo que el
CI imprime y lo que alguien lee a las 3am para saber qué ha roto.
"""

import json

import pytest

from scripts.check_source_ownership import ROOT, validate


def _fuente(nombre, **extra):
    """Una fuente válida mínima, sobre la que cada test cambia UNA cosa.

    Los valores salieron de caracterizar el código, no de suponerlos, y las tres
    suposiciones que traía la primera versión de este fichero eran falsas:
    `core_owned` NO es un estado válido (los cinco son `local_pending`,
    `core_shadow`, `core_primary`, `local_removed` y `retired`) y el campo se
    llama `target_owner`, no `target`. Con la fixture equivocada los tests de
    por-fuente cortocircuitaban en la comprobación del estado y «pasaban» por el
    motivo erróneo. Es justo lo que los tests de caracterización existen para
    evitar: haber refactorizado sobre esas suposiciones habría sido peor.
    """
    base = {
        "name": nombre,
        "state": "local_removed",
        "target_owner": "core",
        "active_publishers": ["core"],
    }
    base.update(extra)
    return base


def _manifiesto(fuentes, **extra):
    base = {"schema_version": 1, "sources": fuentes}
    base.update(extra)
    return base


def _errores(manifiesto):
    """Todos los errores de un manifiesto sintético.

    OJO: con un manifiesto de una sola fuente, la familia de DESCUBRIMIENTO
    también salta («core adapters mismatch: … unexpected=[41 nombres]»), porque
    compara el manifiesto con el árbol real. Por eso los tests de por-fuente
    filtran por el prefijo del nombre en vez de comparar la lista entera: lo que
    fijan es SU mensaje, no el conjunto.
    """
    return validate(manifiesto, None)


class TestFormaDelManifiesto:
    def test_sin_schema_version_1_se_rinde_pronto(self):
        # Y se RINDE, no acumula: sin la lista no hay nada que validar.
        assert _errores({"sources": []}) == [
            "manifest must have schema_version=1 and a sources list"
        ]

    def test_una_fuente_sin_nombre(self):
        errores = _errores(_manifiesto([{"state": "core_owned"}]))
        assert "every source must have a non-empty string name" in errores

    def test_las_fuentes_van_ORDENADAS(self):
        errores = _errores(_manifiesto([_fuente("zeta"), _fuente("alfa")]))
        assert "sources must be sorted by name" in errores

    def test_los_nombres_son_UNICOS(self):
        errores = _errores(_manifiesto([_fuente("misma"), _fuente("misma")]))
        assert "source names must be unique" in errores


class TestInvariantesPorFuente:
    def test_un_estado_desconocido(self):
        errores = _errores(_manifiesto([_fuente("x", state="inventado")]))
        assert any("invalid state" in e for e in errores)

    def test_una_fuente_retirada_no_tiene_destino_ni_publicadores(self):
        errores = _errores(_manifiesto([_fuente("x", state="retired")]))
        assert any("retired sources have no target or publishers" in e for e in errores)

    def test_toda_fuente_no_retirada_apunta_al_nucleo(self):
        errores = _errores(_manifiesto([_fuente("x", target_owner="local")]))
        assert any("every non-retired source targets core" in e for e in errores)

    def test_core_owned_exige_que_el_NUCLEO_sea_el_unico_publicador(self):
        errores = _errores(
            _manifiesto([_fuente("x", active_publishers=["core", "swissjob"])])
        )
        assert any("core as sole publisher" in e for e in errores)

    def test_local_pending_exige_un_publicador_local(self):
        errores = _errores(_manifiesto([_fuente("x", state="local_pending")]))
        assert any("local_pending requires a local publisher" in e for e in errores)

    def test_core_shadow_no_publica_desde_el_nucleo(self):
        errores = _errores(_manifiesto([_fuente("x", state="core_shadow")]))
        assert any("core_shadow must not publish from core" in e for e in errores)

    def test_una_fuente_VALIDA_no_produce_ningun_error_suyo(self):
        """El control que hace discriminar a los de arriba: si la base ya
        fallara, cada test pasaría por el motivo equivocado."""
        errores = _errores(_manifiesto([_fuente("x")]))
        assert [e for e in errores if e.startswith("x:")] == []

    def test_publicadores_duplicados(self):
        errores = _errores(
            _manifiesto([_fuente("x", active_publishers=["core", "core"])])
        )
        assert any("must be a unique list" in e for e in errores)


class TestRatchets:
    """A-70, de la misma auditoría, vive aquí al lado: el default de estos
    `.get()` es el VALOR ACTUAL, así que borrar la clave hace pasar la
    comprobación siempre. Se deja constancia con un test que lo DOCUMENTA, no
    que lo bendiga: cuando se arregle A-70, este test cambia de sentido y es la
    señal de que hay que mirarlo."""

    def test_el_ratchet_de_fuentes_del_nucleo_salta(self):
        errores = _errores(
            _manifiesto([_fuente("x")], ratchet={"min_core_owned_sources": 99})
        )
        assert any("core-owned source count decreased" in e for e in errores)

    def test_el_ratchet_de_inventario_verificado_salta(self):
        errores = _errores(
            _manifiesto([_fuente("x")], ratchet={"min_inventory_verified_sources": 99})
        )
        assert any("verified inventory count decreased" in e for e in errores)

    def test_sin_la_clave_el_ratchet_NO_protege(self):
        """Documenta A-70, no lo aprueba: `max_local_pending_sources` ausente
        toma como tope el valor actual, así que nunca puede fallar."""
        manifiesto = _manifiesto(
            [_fuente("x", state="local_pending", active_publishers=["swissjob"])],
            ratchet={},
        )
        errores = _errores(manifiesto)
        assert not any("local-pending source count increased" in e for e in errores), (
            "si esto falla, A-70 se ha arreglado y hay que actualizar este test"
        )


class TestElManifiestoREALpasa:
    """El control que de verdad protege el refactor: el manifiesto en vigor y el
    árbol en vigor no producen ni un error. Es lo que el CI comprueba."""

    def test_el_repo_tal_como_esta_valida_sin_errores(self):
        manifiesto = json.loads(
            (ROOT / "docs/unificacion/source_ownership.json").read_text(
                encoding="utf-8"
            )
        )
        assert validate(manifiesto, None) == []

    def test_el_manifiesto_esta_donde_main_lo_busca(self):
        assert (ROOT / "docs/unificacion/source_ownership.json").is_file()


class TestLasFamiliasQueLEENelArbol:
    """Descubrimiento, dependencias e imagen de producción leen ficheros reales,
    así que se comprueban invirtiendo el árbol: si el fichero que la familia
    vigila no está donde cree, el guard tiene que decirlo en vez de pasar."""

    @pytest.mark.parametrize(
        "ruta",
        [
            "backend/requirements.txt",
            "jobhunt_core/requirements.txt",
            "backend/Dockerfile.prod",
            "docker-compose.prod.yml",
            "backend/.dockerignore",
        ],
    )
    def test_los_ficheros_que_vigila_EXISTEN(self, ruta):
        # Si uno se mueve, `validate` reventaría con FileNotFoundError en vez de
        # devolver un error legible, y el CI daría un traceback en el paso 1.
        assert (ROOT / ruta).is_file(), f"{ruta} no está donde el guard lo busca"


class TestDescubrimiento:
    """La familia que compara el manifiesto con el ÁRBOL: adaptadores del
    núcleo, módulos del BFF y su registro. Es la razón de ser del guard.

    Estos dos tests existen porque el CONTROL NEGATIVO del refactor los pidió:
    al quitar `_errores_de_descubrimiento` de `validate`, los 22 tests seguían
    en verde. Los sintéticos filtran por el prefijo del nombre y el del
    manifiesto real afirma `== []`, que es lo que la familia produce cuando todo
    cuadra — así que ninguno la tocaba. Un refactor que perdiera esta familia
    habría pasado inadvertido.
    """

    def test_un_manifiesto_que_NO_declara_los_adaptadores_del_nucleo_falla(self):
        # Una sola fuente sin `core_adapter` frente a los 41 del árbol.
        errores = _errores(_manifiesto([_fuente("x")]))
        assert any("core adapters mismatch" in e for e in errores), errores

    def test_el_mensaje_dice_QUE_sobra_y_QUE_falta(self):
        """Un «mismatch» a secas obliga a ir a buscar la diferencia a mano; el
        guard imprime las dos listas, y eso es parte del contrato."""
        errores = _errores(_manifiesto([_fuente("inventada")]))
        mismatch = next(e for e in errores if "core adapters mismatch" in e)
        assert "missing=" in mismatch and "unexpected=" in mismatch

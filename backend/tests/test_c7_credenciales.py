"""C7 — credenciales: inyección de la clave de Redis y guardia de arranque.

Dos cosas que un documento puede prometer y el código no dar:

1. Que las tres URLs de Redis lleven credencial. La trampa medida: Celery lee
   ``CELERY_BROKER_URL`` del ENTORNO y esa variable gana sobre lo que le pasa
   el código, así que una URL sin contraseña en `.env` dejaba al worker fuera
   (NOAUTH) mientras el BFF entraba sin problema. Por eso esas variables ya no
   se declaran en `.env`: se construyen aquí, en un solo sitio.
2. Que el arranque se niegue con credenciales de desarrollo. Cada caso de
   abajo prueba SU condición, no «que algo falle».
"""

import pytest

import main
from config import Settings


def _url(password: str) -> str:
    credencial = f"swissjob:{password}" if password else "swissjob"
    return f"postgresql+asyncpg://{credencial}@postgres:5432/swissjobhunter"


def _ajustes(password: str, *, permiso: bool = False) -> Settings:
    """Ajustes con el permiso FIJADO, no heredado del entorno.

    El `.env` de esta máquina declara `ALLOW_DEV_CREDENTIALS=true`, y compose lo
    mete en el entorno del contenedor donde corren los tests: sin fijarlo aquí,
    los diez casos de «aborta» pasaban en verde con la guardia desarmada.
    """
    return Settings(DATABASE_URL=_url(password), ALLOW_DEV_CREDENTIALS=permiso)


class TestInyeccionDeLaClaveDeRedis:
    def test_sin_clave_las_urls_quedan_intactas(self):
        s = Settings(REDIS_PASSWORD="")
        assert s.REDIS_URL == "redis://redis:6379/0"
        assert s.CELERY_BROKER_URL == "redis://redis:6379/1"
        assert s.CELERY_RESULT_BACKEND == "redis://redis:6379/2"

    def test_con_clave_la_llevan_las_tres(self):
        s = Settings(REDIS_PASSWORD="s3cr3t")
        assert s.REDIS_URL == "redis://:s3cr3t@redis:6379/0"
        assert s.CELERY_BROKER_URL == "redis://:s3cr3t@redis:6379/1"
        assert s.CELERY_RESULT_BACKEND == "redis://:s3cr3t@redis:6379/2"

    def test_una_url_con_credencial_propia_se_respeta(self):
        """Quien la escribe explícitamente manda sobre el atajo."""
        s = Settings(
            REDIS_PASSWORD="s3cr3t",
            REDIS_URL="redis://usuario:otra@otro-redis:6379/9",
        )
        assert s.REDIS_URL == "redis://usuario:otra@otro-redis:6379/9"
        assert s.CELERY_BROKER_URL == "redis://:s3cr3t@redis:6379/1"

    def test_los_caracteres_especiales_se_escapan(self):
        """Una clave con `@`, `/` o `:` rompería la URL si se pegara cruda."""
        s = Settings(REDIS_PASSWORD="a@b/c:d")
        assert s.REDIS_URL == "redis://:a%40b%2Fc%3Ad@redis:6379/0"

    def test_no_toca_esquemas_ajenos(self):
        s = Settings(REDIS_PASSWORD="s3cr3t", CELERY_BROKER_URL="amqp://conejo:5672")
        assert s.CELERY_BROKER_URL == "amqp://conejo:5672"


class TestGuardiaDeCredencialesDeLaBase:
    """Se invoca con `en_pruebas=False` a propósito.

    Borrar `PYTEST_CURRENT_TEST` en un fixture NO funciona: pytest la vuelve a
    escribir al entrar en la fase de ejecución del test, así que la guardia se
    saltaba y los diez casos pasaban en verde sin comprobar nada. Se midió: con
    el fixture, 10 fallos; el atajo estaba ganando.
    """

    @pytest.mark.parametrize(
        "password",
        ["swissjob_dev_2024", "jobhunt_core_dev", "postgres", "password"],
        ids=["postgres legacy", "core", "trivial postgres", "trivial password"],
    )
    def test_aborta_con_contrasena_de_dev(self, monkeypatch, password):
        monkeypatch.setattr(main, "settings", _ajustes(password))
        with pytest.raises(RuntimeError, match="contraseña de desarrollo"):
            main._validate_database_credentials(en_pruebas=False)

    @pytest.mark.parametrize(
        "password",
        ["__CAMBIAME__", "CHANGE_ME", "changeme", "PLACEHOLDER", "example-pass"],
    )
    def test_aborta_con_marcador_de_plantilla(self, monkeypatch, password):
        monkeypatch.setattr(main, "settings", _ajustes(password))
        with pytest.raises(RuntimeError):
            main._validate_database_credentials(en_pruebas=False)

    def test_aborta_sin_contrasena(self, monkeypatch):
        monkeypatch.setattr(main, "settings", _ajustes(""))
        with pytest.raises(RuntimeError):
            main._validate_database_credentials(en_pruebas=False)

    def test_pasa_con_contrasena_real(self, monkeypatch):
        monkeypatch.setattr(main, "settings", _ajustes("k7Qp2mX9vL4nR8sT"))
        main._validate_database_credentials(en_pruebas=False)

    def test_el_permiso_explicito_la_desarma(self, monkeypatch):
        """ALLOW_DEV_CREDENTIALS es lo que distingue un portátil de un servidor."""
        monkeypatch.setattr(
            main, "settings", _ajustes("swissjob_dev_2024", permiso=True)
        )
        main._validate_database_credentials(en_pruebas=False)

    def test_bajo_pytest_no_estorba(self, monkeypatch):
        """Control del control: sin este atajo la suite entera no arrancaría.

        Aquí SÍ se deja que la guardia mire el entorno, que es lo que se prueba.
        """
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_algo")
        monkeypatch.setattr(main, "settings", _ajustes("postgres"))
        main._validate_database_credentials()

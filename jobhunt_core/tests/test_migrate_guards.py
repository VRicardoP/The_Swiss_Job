"""Guardas de seguridad del bootstrap (A-01, auditoría Opus).

CREATE ROLE / CREATE SCHEMA son DDL sin parámetros bind: la ÚNICA barrera
contra inyección son _PW_RE/_IDENT_RE. Estos tests fijan esa barrera y el
fail-fast de credenciales de dev en prod.
"""

import pytest

from jobhunt_core.migrate import _IDENT_RE, _PW_RE, _bootstrap


class TestLiteralGuards:
    def test_pw_accepts_default_and_sane(self):
        assert _PW_RE.match("jobhunt_core_dev")
        assert _PW_RE.match("A1b2-C3d4_E5f6")

    def test_pw_rejects_injection_vectors(self):
        # comilla/backslash/espacios permitirían escapar del literal SQL.
        for bad in ["pw'; DROP ROLE x;--", 'pw"x', "pw\\x", "pw con espacios", "corta"]:
            assert not _PW_RE.match(bad), bad

    def test_ident_accepts_schema_and_rejects_injection(self):
        assert _IDENT_RE.match("jobhunt")
        for bad in ['jobhunt"; --', "Jobhunt", "1jobhunt", "job-hunt", "job hunt"]:
            assert not _IDENT_RE.match(bad), bad

    def test_bootstrap_raises_on_bad_password(self, monkeypatch):
        # Valida ANTES de conectar: no hace falta BD para este test.
        monkeypatch.setenv("CORE_ADMIN_DATABASE_URL", "postgresql://x@nowhere/db")
        monkeypatch.setenv("CORE_DB_PASSWORD", "bad pass!'")
        with pytest.raises(ValueError):
            _bootstrap()


class TestProdFailFast:
    """rev. externa A-01 #2: el DoD de aislamiento se IMPONE en prod, no se asume."""

    GOOD = {
        "CORE_ENV": "prod",
        "CORE_DATABASE_URL": "postgresql+asyncpg://jobhunt_core:RealPw_12345@postgres:5432/swissjobhunter",
        "CORE_BROKER_URL": "redis://:RealRedisPw_1@redis-core:6379/0",
        "CORE_RESULT_BACKEND": "redis://:RealRedisPw_1@redis-core:6379/1",
    }

    def _settings(self, monkeypatch, **overrides):
        from jobhunt_core.config import CoreSettings

        for k, v in {**self.GOOD, **overrides}.items():
            monkeypatch.setenv(k, v)
        return CoreSettings

    def test_valid_prod_config_passes(self, monkeypatch):
        cls = self._settings(monkeypatch)
        assert cls().CORE_ENV == "prod"

    def test_dev_credential_rejected_in_prod(self, monkeypatch):
        from jobhunt_core.config import CoreSettings

        monkeypatch.setenv("CORE_ENV", "prod")
        with pytest.raises(Exception):  # ValidationError de Pydantic
            CoreSettings()  # sin CORE_DATABASE_URL → caería al default de dev

    def test_legacy_db_user_rejected_in_prod(self, monkeypatch):
        cls = self._settings(
            monkeypatch,
            CORE_DATABASE_URL="postgresql+asyncpg://swissjob:RealPw_12345@postgres:5432/swissjobhunter",
        )
        with pytest.raises(Exception):
            cls()  # usuario legacy/admin → rompe el rol de mínimo privilegio

    def test_legacy_redis_broker_rejected_in_prod(self, monkeypatch):
        cls = self._settings(
            monkeypatch, CORE_BROKER_URL="redis://:RealRedisPw_1@redis:6379/1"
        )
        with pytest.raises(Exception):
            cls()  # Redis de caché legacy (allkeys-lru) → rompe el broker dedicado

    def test_dev_redis_password_rejected_in_prod(self, monkeypatch):
        cls = self._settings(
            monkeypatch, CORE_BROKER_URL="redis://:core_redis_dev@redis-core:6379/0"
        )
        with pytest.raises(Exception):
            cls()

    def test_dev_default_is_fine_in_dev(self, monkeypatch):
        from jobhunt_core.config import CoreSettings

        for k in self.GOOD:
            monkeypatch.delenv(k, raising=False)
        assert CoreSettings().CORE_ENV == "dev"

    def test_unknown_env_value_rejected(self, monkeypatch):
        # rev. #3: "production"/"PROD"/etc. NO desactivan las guardas en
        # silencio — CORE_ENV es Literal["dev","prod"] y cualquier otro valor
        # invalida la configuración entera.
        cls = self._settings(monkeypatch, CORE_ENV="production")
        with pytest.raises(Exception):
            cls()

    def test_template_placeholders_rejected_in_prod(self, monkeypatch):
        # rev. #3: la plantilla .env.core.prod.example SIN rellenar no debe
        # arrancar en prod (valores replicados literalmente de la plantilla).
        cls = self._settings(
            monkeypatch,
            CORE_DATABASE_URL=(
                "postgresql+asyncpg://jobhunt_core:CAMBIA_PASSWORD_DB"
                "@postgres:5432/swissjobhunter"
            ),
            CORE_BROKER_URL="redis://:CAMBIA_PASSWORD_REDIS@redis-core:6379/0",
            CORE_RESULT_BACKEND="redis://:CAMBIA_PASSWORD_REDIS@redis-core:6379/1",
        )
        with pytest.raises(Exception):
            cls()

    def test_wrong_url_scheme_rejected_in_prod(self, monkeypatch):
        cls = self._settings(
            monkeypatch,
            CORE_DATABASE_URL="postgresql://jobhunt_core:RealPw_12345@postgres:5432/swissjobhunter",
        )
        with pytest.raises(Exception):
            cls()  # sin +asyncpg → driver equivocado


class TestPasswordCrossCheck:
    def test_bootstrap_rejects_mismatched_passwords(self, monkeypatch):
        # rev. #5: CORE_DB_PASSWORD debe coincidir con la contraseña de
        # CORE_DATABASE_URL; si no, la rotación desincronizaría rol y clientes.
        # (El default de CORE_DATABASE_URL lleva jobhunt_core_dev.)
        monkeypatch.setenv("CORE_ADMIN_DATABASE_URL", "postgresql://x@nowhere/db")
        monkeypatch.setenv("CORE_DB_PASSWORD", "OtraPassword_123")
        with pytest.raises(ValueError, match="no coincide"):
            _bootstrap()

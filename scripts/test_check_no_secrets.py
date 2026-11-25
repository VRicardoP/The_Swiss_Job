from pathlib import Path

from check_no_secrets import revisar


def test_fixed_refresh_body_flag_is_not_a_secret(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("AUTH_REFRESH_TOKEN_IN_BODY: 'false'\n")
    assert revisar(compose) == []


def test_refresh_body_flag_only_allows_the_safe_fixed_value(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("AUTH_REFRESH_TOKEN_IN_BODY: 'true'\n")
    assert revisar(compose) == [
        f"{compose}:1: AUTH_REFRESH_TOKEN_IN_BODY lleva un literal"
    ]


def test_other_secret_like_keys_still_reject_false_literal(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("SMTP_PASSWORD: 'false'\n")
    assert revisar(compose) == [f"{compose}:1: SMTP_PASSWORD lleva un literal"]


# --- Sintaxis `.env` (auditoría 2026-11-02, A-02) -------------------------
#
# El guard sólo entendía YAML: sus dos regex exigen `:` como separador. Una
# contraseña de producción se fue a `docs/DEPLOY_NAS.md` en sintaxis `.env`
# (`CLAVE=valor`) y, apuntado a ese fichero, el guard devolvía «0 hallazgos».
# Control negativo de estos casos: quitando `CLAVE_SENSIBLE_ENV` de `revisar`,
# los cuatro primeros fallan.


def test_env_syntax_literal_es_un_hallazgo(tmp_path: Path):
    doc = tmp_path / "runbook.md"
    doc.write_text("POSTGRES_PASSWORD=aB3xK9mQ7tR2wL5p\n")
    assert revisar(doc) == [f"{doc}:1: POSTGRES_PASSWORD lleva un literal"]


def test_env_syntax_tambien_en_yaml_y_con_export(tmp_path: Path):
    doc = tmp_path / "notas.md"
    doc.write_text("export SECRET_KEY=aB3xK9mQ7tR2wL5p\n")
    assert revisar(doc) == [f"{doc}:1: SECRET_KEY lleva un literal"]


def test_url_con_credencial_embebida_es_un_hallazgo(tmp_path: Path):
    doc = tmp_path / "runbook.md"
    doc.write_text(
        "DATABASE_URL=postgresql+asyncpg://u:aB3xK9mQ7tR2wL5p@host:5432/db\n"
    )
    assert revisar(doc) == [
        f"{doc}:1: DATABASE_URL lleva una URL con credencial embebida"
    ]


def test_url_sin_credencial_no_es_un_hallazgo(tmp_path: Path):
    # `redis://redis:6379/0` no es un secreto; marcarlo entrenaría a ignorar.
    doc = tmp_path / "runbook.md"
    doc.write_text(
        "REDIS_URL=redis://redis:6379/0\nCORE_DSN=sqlite+aiosqlite:///x.db\n"
    )
    assert revisar(doc) == []


def test_marcadores_de_documentacion_no_son_hallazgos(tmp_path: Path):
    doc = tmp_path / "runbook.md"
    doc.write_text(
        "POSTGRES_PASSWORD=<el-mismo-en-las-dos-líneas>\n"
        "GROQ_API_KEY=gsk_...\n"
        "SMTP_PASSWORD=\n"
        "SECRET_KEY=${SECRET_KEY}\n"
        "CORE_DSN=postgresql+asyncpg://jobhunt_core:***@host/db\n"
        "ADMIN_PASSWORD=audit-not-a-real-secret\n"
    )
    assert revisar(doc) == []


def test_asignaciones_en_linea_de_un_comando_no_son_hallazgos(tmp_path: Path):
    # `.*$` se tragaba el resto de la línea y el conjunto parecía credencial.
    doc = tmp_path / "runbook.md"
    doc.write_text(
        "POSTGRES_PASSWORD=ci REDIS_PASSWORD=ci docker compose -f docker-compose.yml config -q\n"
    )
    assert revisar(doc) == []


def test_anotacion_de_python_citada_en_markdown_no_es_un_hallazgo(tmp_path: Path):
    # `clave: valor` es también la forma de una anotación de Python. Las regex
    # de YAML sólo se aplican a `.yml`/`.yaml` por esto.
    doc = tmp_path / "plan.md"
    doc.write_text('GLASSDOOR_PARTNER_KEY: str = ""\n')
    assert revisar(doc) == []


def test_en_yaml_la_anotacion_si_se_revisa(tmp_path: Path):
    # Contraparte del anterior: en un compose, un literal es un literal.
    compose = tmp_path / "compose.yml"
    compose.write_text("SMTP_PASSWORD: hunter2hunter2\n")
    assert revisar(compose) == [f"{compose}:1: SMTP_PASSWORD lleva un literal"]


# --- Elementos de lista YAML (2026-11-25) ---------------------------------
#
# Container Station lleva la contraseña de Redis como `- <valor>` bajo
# `command:` y `healthcheck.test:`. Ninguna regla anterior lo veía porque todas
# exigen `clave:`. Control negativo: quitando `ELEMENTO_DE_LISTA` de `revisar`,
# el primer caso falla.


def test_literal_en_elemento_de_lista_es_un_hallazgo(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  redis:\n    command:\n    - redis-server\n    - --requirepass\n"
        "    - Zx9Kq2Lm8Pw4Rt6Yv1Bn3Cd5Fg7Hj0As\n"
    )
    assert revisar(compose) == [
        f"{compose}:6: elemento de lista con pinta de credencial"
    ]


def test_elementos_de_lista_legitimos_no_son_hallazgos(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "    - ${REDIS_PASSWORD}\n"
        "    - --maxmemory-policy\n"
        "    - swissjob-net\n"
        "    - default,ai\n"
        "    - /share/external/DEV3301_1/apps-ssd/swissjob-deploy/docker/postgres/"
        "init-pgvector.sql:/docker-entrypoint-initdb.d/init-pgvector.sql:ro\n"
        "    - /share/external/DEV3301_1/apps-ssd/swissjob-deploy/.env.postgres.runtime\n"
        "    - jobhunt_core.tasks.celery_app\n"
    )
    assert revisar(compose) == []


def test_alias_de_red_en_lista_no_es_un_hallazgo(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "        aliases:\n        - swissjob-core-api-r5\n    - swissjob-r5_core-net\n"
    )
    assert revisar(compose) == []

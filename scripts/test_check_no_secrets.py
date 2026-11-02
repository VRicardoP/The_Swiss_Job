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

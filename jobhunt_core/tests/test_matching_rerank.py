"""Señales del rerank v5 (unidad, sin BD) — familias de regresión del cierre.

Cada familia reproduce el defecto observado en el desarrollo (falsos positivos
bilingües genéricos, ofertas «remote» ancladas a un estado, requisitos de
idioma en el título) y su INVERSO legítimo (dato ausente = neutral, jamás
exclusión): la precisión no puede comprarse con recall.
"""

import pytest

from jobhunt_core import matching

RECETA = {
    "algorithm": "hybrid_rrf_rerank",
    "lexical_query": "v2",
    "lexical_weight": 0.25,
    "rrf_k": 60,
    "rerank": "v1",
    "role_theta": 0.25,
    "role_w": 1,
    "role_a": 0.5,
    "p_loc": 0.4,
    "p_lang": 0.4,
    "geo_lexicon": "v1",
    "lang_lexicon": "v1",
}
PREFS = {
    "languages": ["English", "Spanish"],
    "remote_pref": "remote_only",
    "_compat": frozenset({"switzerland", "spain"}),
}


def _score(
    base,
    sim=0.0,
    titulo="Specialist",
    location=None,
    remote=True,
    prefs=PREFS,
    receta=RECETA,
):
    s, comp = matching._rerank_score(base, sim, titulo, location, remote, prefs, receta)
    return s, comp


# --- familia 3: remote=true con país/estado concreto NO es global


@pytest.mark.parametrize(
    "location,paises",
    [
        ("Texas (USA)", {"usa"}),
        ("Nevada (USA), Oregon (USA)", {"usa"}),
        ("Canada", {"canada"}),
        ("California (USA)", {"usa"}),
        ("Colombia, Mexico", {"colombia", "mexico"}),
        ("Brazil", {"brazil"}),
    ],
)
def test_una_oferta_remota_anclada_a_paises_incompatibles_se_penaliza(location, paises):
    assert matching._offer_countries(location) == frozenset(paises)
    s_anclada, comp = _score(70.0, location=location)
    s_global, _ = _score(70.0, location="Anywhere in the World")
    assert comp["loc_incompatible"] is True
    assert s_anclada < s_global


@pytest.mark.parametrize(
    "location",
    [
        "Anywhere in the World",
        "International",
        "Global",
        "Remote",
        "",
        None,
        "Rathcoole",  # ciudad fuera de léxico ⇒ neutral, no exclusión (familia 5)
        "Indiana Occidental",  # «india» NO dispara dentro de «indiana»… pero
    ],
)
def test_global_o_no_parseado_es_neutral(location):
    if location == "Indiana Occidental":
        # …«indiana» SÍ es estado USA: el caso prueba la frontera de palabra
        assert matching._offer_countries(location) == frozenset({"usa"})
        return
    assert matching._offer_countries(location) == frozenset()
    _, comp = _score(70.0, location=location)
    assert comp["loc_incompatible"] is False


@pytest.mark.parametrize(
    "location",
    [
        "Germany / Switzerland",  # compatible al final
        "Switzerland, Canada",  # compatible al principio
        "Canada, Spain, Brazil",  # compatible entre dos incompatibles
        "Bosnia and Herzegovina, Switzerland",  # multi-palabra + compatible
    ],
)
def test_multipais_con_interseccion_es_compatible(location):
    """P2 revisión 2026-09-03: quedarse con el PRIMER país convertía
    «Germany / Switzerland» en incompatible para un perfil suizo."""
    _, comp = _score(70.0, location=location)  # PREFS compat={switzerland, spain}
    assert comp["loc_incompatible"] is False


def test_multipais_sin_interseccion_sigue_siendo_incompatible():
    _, comp = _score(70.0, location="Germany / Canada")
    assert comp["loc_incompatible"] is True


def test_remote_only_contra_oferta_presencial_es_incompatibilidad():
    s_onsite, comp = _score(70.0, remote=False, location="Zurich")
    assert comp["loc_incompatible"] is True
    # modalidad desconocida (remote=None) ⇒ neutral
    _, comp2 = _score(70.0, remote=None, location="Zurich")
    assert comp2["loc_incompatible"] is False


def test_un_perfil_sin_paises_declarados_no_excluye_nada():
    prefs = dict(PREFS, _compat=frozenset())
    _, comp = _score(70.0, location="Texas (USA)", prefs=prefs)
    assert comp["loc_incompatible"] is False


# --- familia 4: idioma del título es requisito; incidental/desconocido no


def test_idioma_del_titulo_no_cubierto_penaliza():
    s_fr, comp = _score(70.0, titulo="Customer Service Representative English & French")
    assert comp["lang_missing"] == ["french"]
    s_es, comp2 = _score(70.0, titulo="Bilingual-Spanish Support Specialist")
    assert comp2["lang_missing"] == []  # Spanish está en el perfil
    assert s_fr < s_es


def test_idiomas_disyuntivos_no_exigen_ambos():
    """P2 revisión 2026-09-03: «English or Spanish» penalizaba a quien
    declara inglés porque se interpretaba como conjunción."""
    # el perfil (English, Spanish) cubre la disyunción: neutral
    _, comp = _score(70.0, titulo="English or Spanish Support Agent")
    assert comp["lang_missing"] == []
    # perfil sin NINGUNA de las alternativas: sí penaliza
    prefs = dict(PREFS, languages=["Japanese"])
    _, comp2 = _score(70.0, titulo="English or French Support Agent", prefs=prefs)
    assert sorted(comp2["lang_missing"]) == ["english", "french"]
    # conjunción explícita: el perfil debe cubrir TODAS
    _, comp3 = _score(70.0, titulo="English and French Support Agent")
    assert comp3["lang_missing"] == ["french"]


def test_idiomas_con_barra_o_mezcla_son_neutrales():
    _, comp = _score(70.0, titulo="English/French Customer Advisor")
    assert comp["lang_missing"] == []
    _, comp2 = _score(70.0, titulo="German and English or French Support")  # mezcla
    assert comp2["lang_missing"] == []


def test_perfil_sin_idiomas_declarados_es_neutral():
    prefs = dict(PREFS, languages=[])
    _, comp = _score(70.0, titulo="French Support Agent", prefs=prefs)
    assert comp["lang_missing"] == []


def test_titulo_sin_idiomas_es_neutral():
    _, comp = _score(70.0, titulo="Quality Assurance Rater")
    assert comp["lang_missing"] == []


# --- familias 1-2: la señal de rol adelanta al genérico bilingüe


def test_un_rol_explicito_supera_a_un_generico_con_mas_base():
    generico, _ = _score(70.0, sim=0.10, titulo="Bilingual Specialist")
    explicito, _ = _score(60.0, sim=0.60, titulo="Customer Success Manager")
    assert explicito > generico


def test_la_familia_aditiva_rescata_un_rango_profundo():
    # base ínfima (rango ~1000 en RRF) + rol claro: el término aditivo lo
    # sube por encima de un mediocre sin señal — el multiplicativo solo no.
    solo_mult = dict(RECETA, role_a=0)
    hundido_m, _ = _score(5.0, sim=0.60, receta=solo_mult)
    mediocre_m, _ = _score(40.0, sim=0.0, receta=solo_mult)
    assert hundido_m < mediocre_m  # el defecto que motiva la familia E
    # con a=1.0 (la intensidad medida en las ablaciones) el aditivo lo rescata
    aditiva = dict(RECETA, role_a=1.0)
    hundido_a, _ = _score(5.0, sim=0.60, receta=aditiva)
    mediocre_a, _ = _score(40.0, sim=0.0, receta=aditiva)
    assert hundido_a > mediocre_a


def test_la_escala_evita_la_saturacion_del_clamp():
    """Sin la normalización, dos scores brutos >100 empatarían en 100.00 y el
    orden real se perdería en silencio."""
    escala = matching._rerank_scale(RECETA)
    a, _ = _score(80.0, sim=0.9)
    b, _ = _score(78.0, sim=0.9)
    assert a > b > 100  # brutos por encima de 100…
    na = round(min(100.0, max(0.0, a / escala * 100)), 2)
    nb = round(min(100.0, max(0.0, b / escala * 100)), 2)
    assert 0 < nb < na <= 100  # …y normalizados conservan el orden bajo 100


# --- familia 8: receta incompleta/incompatible falla antes de evaluar


@pytest.mark.parametrize(
    "mala",
    [
        dict(RECETA, rerank="v9"),
        dict(RECETA, geo_lexicon="v9"),
        dict(RECETA, lang_lexicon="v9"),
        dict(RECETA, role_theta=1.5),
        dict(RECETA, p_loc=1.0),
        dict(RECETA, role_w=float("nan")),
        {k: v for k, v in RECETA.items() if k != "p_lang"},
        dict(RECETA, extra=1),
    ],
)
def test_recetas_rerank_invalidas_no_pasan(mala):
    with pytest.raises(ValueError):
        matching._validated_rerank_recipe(mala)


def test_todas_las_senales_apagadas_reproduce_el_orden_v4():
    """role_w=role_a=p_loc=p_lang=0 ⇒ score = base normalizada: mismo ORDEN
    que v4 (equivalencia de la configuración nula)."""
    apagada = dict(RECETA, role_w=0, role_a=0, p_loc=0, p_lang=0)
    bases = [80.0, 70.0, 60.0]
    scores = [
        _score(
            b, sim=0.9, titulo="French Agent", location="Texas (USA)", receta=apagada
        )[0]
        for b in bases
    ]
    assert scores == bases  # sin señales, la puntuación ES la base


# --- receta cross_encoder v3 (fine-tuned): procedencia obligatoria


def test_receta_finetuned_exige_procedencia_y_ruta_local():
    base = dict(matching.XENC_POLICY_WEIGHTS)
    # local sin procedencia ⇒ rechazo
    with pytest.raises(ValueError, match="train_data_sha256"):
        matching._validated_cross_encoder_recipe(dict(base, model="/modelos/x"))
    # procedencia con modelo de hub ⇒ rechazo (debe ser el artefacto sellado)
    with pytest.raises(ValueError, match="RUTA"):
        matching._validated_cross_encoder_recipe(dict(base, train_data_sha256="a" * 64))
    # v3 bien formada ⇒ pasa
    ok = matching._validated_cross_encoder_recipe(
        dict(
            base,
            model="/modelos/x",
            model_fingerprint="b" * 64,
            train_data_sha256="a" * 64,
        )
    )
    assert ok["train_data_sha256"] == "a" * 64
    # sha inválido ⇒ rechazo
    with pytest.raises(ValueError, match="sha256"):
        matching._validated_cross_encoder_recipe(
            dict(base, model="/modelos/x", train_data_sha256="zz")
        )

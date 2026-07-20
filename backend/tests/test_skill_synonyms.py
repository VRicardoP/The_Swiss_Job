"""Tests del filtro de skills 'missing' falsas por sinonimia."""

from services.skill_synonyms import filter_missing_skills


def test_filtra_sinonimo_equivalente():
    # El candidato pone "copywriting"; la oferta pide "content writer" (mismo canónico).
    result = filter_missing_skills(["Copywriting"], ["Content Writer"])
    assert result == []


def test_conserva_missing_real():
    result = filter_missing_skills(["copywriting"], ["Kubernetes"])
    assert result == ["Kubernetes"]


def test_case_insensitive_y_preserva_original():
    result = filter_missing_skills(
        ["HR", "Excel"], ["Recruiting", "Kubernetes", "excel"]
    )
    # "Recruiting" es variante de "human resources" (candidato tiene HR) → fuera.
    # "excel" coincide con MS Office del candidato (Excel) por sinónimo/exacto → fuera.
    # "Kubernetes" se mantiene con su casing original.
    assert result == ["Kubernetes"]


def test_missing_vacio_devuelve_vacio():
    assert filter_missing_skills(["hr"], []) == []


def test_skill_desconocida_solo_se_filtra_por_coincidencia_exacta():
    # "Terraform" no está en el mapa; solo se quita si el candidato la lista igual.
    assert filter_missing_skills(["python"], ["Terraform"]) == ["Terraform"]
    assert filter_missing_skills(["terraform"], ["Terraform"]) == []

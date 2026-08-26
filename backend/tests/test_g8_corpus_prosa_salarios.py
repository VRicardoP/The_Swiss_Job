"""G8/P2-1 y G8/P3-6 — el corpus de PROSA del parser de salarios.

POR QUÉ EXISTE ESTE FICHERO
---------------------------
El parser de salarios ha roto CUATRO veces seguidas por el mismo sitio (la
regla de desempate) y las cuatro versiones se validaron contra el mismo
material: los 637 valores distintos de `jobs.salary_original` en producción,
todo ASCII generado por máquina («107500-204500 USD»). Ese corpus **no puede
refutar nada**: no contiene ni una referencia, ni un año, ni una escala
salarial, ni un `bis`. La auditoría G8 construyó por fin el corpus que faltaba
—62 anuncios de prosa realista— y con él encontró que la versión de G7 era peor
que sus TRES predecesores en 8 formas. Este fichero lo incorpora a la suite
para que el quinto intento se pueda contradecir.

CÓMO ESTÁ ORGANIZADO
--------------------
- `CORPUS_PROSA`: los 62 anuncios con el juicio HUMANO de lo que dicen.
- `COTAS_CONOCIDAS`: los que el parser de hoy **no** acierta, cada uno con su
  razón. Van como `xfail(strict=True)`: si un ciclo futuro los arregla, la
  suite AVISA (XPASS = fallo) en vez de dejarlo pasar en silencio.
- `test_familia_ruido_de_cuatro_cifras`: las nueve formas de la familia que
  G8/P2-1 encontró. Es la MORDIDA — sin el ancla léxica, las nueve fallan.
"""

import pytest

from services.data_normalizer import DataNormalizer

# (id, texto, min, max, divisa, nota). `None, None` = «aquí no hay salario».
CORPUS_PROSA = [
    # --- A. Escalas salariales UK/IE (tes.com es productor VIVO) ---
    (
        "A1",
        "Main Pay Scale 1 - 6 (£31,650 - £43,607 per annum)",
        31650,
        43607,
        "GBP",
        "MPS + importe",
    ),
    (
        "A2",
        "MPS/UPS (£31,650 - £49,084) depending on experience",
        31650,
        49084,
        "GBP",
        "escala sin numeros",
    ),
    (
        "A3",
        "Grade 6 - 8, salary £30,000 - £40,000 per annum",
        30000,
        40000,
        "GBP",
        "G7/P2-2 canonico",
    ),
    (
        "A4",
        "NJC Scale Point 18 - 22, £27,334 - £30,296 pro rata",
        27334,
        30296,
        "GBP",
        "NJC + puntos",
    ),
    (
        "A5",
        "Band 5 - 7 of the Agenda for Change, €45,000 to €55,000",
        45000,
        55000,
        "EUR",
        "banda + importe",
    ),
    (
        "A6",
        "Teacher Main Scale 1-6: 31,650 - £43,607",
        31650,
        43607,
        "GBP",
        "divisa solo a la derecha",
    ),
    (
        "A7",
        "Point 12 - €35,000 per annum",
        35000,
        35000,
        "EUR",
        "cota aparcada G5/P3-5",
    ),
    (
        "A8",
        "Salary Scale: Point 1 (€38,000) to Point 10 (€52,000)",
        38000,
        52000,
        "EUR",
        "escala con parentesis",
    ),
    ("A9", "Grade: SO1 (£33,024 - £34,834)", 33024, 34834, "GBP", "grado alfanumerico"),
    (
        "A10",
        "Leadership Scale L11 - L17, £57,000 - £66,000",
        57000,
        66000,
        "GBP",
        "escala L",
    ),
    (
        "A11",
        "Unqualified Teacher Scale 1 - 6 (£20,598 - £32,240)",
        20598,
        32240,
        "GBP",
        "escala UQT",
    ),
    # --- B. Referencias, notas al pie, IDs ---
    (
        "B1",
        "Ref. 2024 - 1187. Salary 80,000 - CHF 100,000 per annum",
        80000,
        100000,
        "CHF",
        "numero de referencia delante",
    ),
    (
        "B2",
        "Job ID 4521-9987 | Gehalt: CHF 95'000 - CHF 115'000",
        95000,
        115000,
        "CHF",
        "id con guion",
    ),
    (
        "B3",
        "Position 3 of 5 available. £45,000 - £52,000",
        45000,
        52000,
        "GBP",
        "conteo delante",
    ),
    (
        "B4",
        "(1) Salaire annuel brut: CHF 92'000 - CHF 108'000. (2) 13e salaire inclus.",
        92000,
        108000,
        "CHF",
        "nota al pie numerada",
    ),
    (
        "B5",
        "Stellen-Nr. 12 - 34 / Jahreslohn CHF 85'000 - CHF 95'000",
        85000,
        95000,
        "CHF",
        "Stellen-Nr",
    ),
    # --- C. Rangos por experiencia / años / pensum ---
    (
        "C1",
        "2 - 5 years experience required. Salary 90,000 - CHF 120,000",
        90000,
        120000,
        "CHF",
        "anios delante",
    ),
    (
        "C2",
        "3 bis 5 Jahre Erfahrung. Lohn: CHF 100'000 - CHF 130'000",
        100000,
        130000,
        "CHF",
        "anios DE",
    ),
    (
        "C3",
        "Teilzeit 60 - 80 Prozent, Jahreslohn 90000 - CHF 110000",
        90000,
        110000,
        "CHF",
        "pensum en palabras",
    ),
    (
        "C4",
        "Pensum 60-80%, CHF 90'000 - CHF 110'000 (100%)",
        90000,
        110000,
        "CHF",
        "pensum con %",
    ),
    (
        "C5",
        "Team of 8 - 12 people. Salary: €60,000 - €75,000",
        60000,
        75000,
        "EUR",
        "tamano de equipo",
    ),
    (
        "C6",
        "Class sizes of 20 - 24 pupils. £34,000 - £41,000 per annum",
        34000,
        41000,
        "GBP",
        "tamano de clase",
    ),
    # --- D. Divisa a ambos lados / paréntesis / conversión ---
    (
        "D1",
        "90000 - 110000 par an (env. CHF 7,500 - CHF 9,200 par mois)",
        90000,
        110000,
        "CHF",
        "G7/P3-1",
    ),
    (
        "D2",
        "CHF 92'000 - CHF 110'000 (approx. £82,000 - £98,000)",
        92000,
        110000,
        "CHF",
        "conversion detras",
    ),
    (
        "D3",
        "(circa £75,000 - £90,000) — CHF 90'000 - CHF 110'000",
        75000,
        90000,
        "GBP",
        "conversion DELANTE",
    ),
    (
        "D4",
        "80'000 - 100'000 (Bonus: CHF 5,000 - CHF 20,000)",
        80000,
        100000,
        "CHF",
        "bonus detras",
    ),
    (
        "D5",
        "Base CHF 120'000 - CHF 140'000 plus bonus of CHF 10'000 - CHF 30'000",
        120000,
        140000,
        "CHF",
        "base+bonus",
    ),
    (
        "D6",
        "£30,000 - 40,000 per annum",
        30000,
        40000,
        "GBP",
        "divisa solo a la IZQUIERDA",
    ),
    (
        "D7",
        "30,000 - £40,000 per annum",
        30000,
        40000,
        "GBP",
        "divisa solo a la derecha",
    ),
    (
        "D8",
        "EUR 45.000 - EUR 55.000 pro Jahr",
        45000,
        55000,
        "EUR",
        "punto como separador de miles",
    ),
    (
        "D9",
        "from 80'000 to 110'000 CHF",
        80000,
        110000,
        "CHF",
        "from..to, divisa al final",
    ),
    (
        "D10",
        "Wir suchen eine Lehrperson. 90'000 - 110'000 CHF (env. £75,000 - £90,000)",
        90000,
        110000,
        "CHF",
        "plain en MEDIO de la prosa",
    ),
    # --- E. Separadores suizos / finos / tipográficos ---
    ("E1", "CHF 80 000 - CHF 100 000", 80000, 100000, "CHF", "U+202F narrow nbsp"),
    ("E2", "CHF 80 000 – CHF 100 000", 80000, 100000, "CHF", "U+2009 thin + en dash"),
    ("E3", "CHF 80 000 - CHF 100 000", 80000, 100000, "CHF", "U+00A0"),
    ("E4", "CHF 80′000 - CHF 100′000", 80000, 100000, "CHF", "prime U+2032"),
    (
        "E5",
        "CHF 80’000 — CHF 100’000",
        80000,
        100000,
        "CHF",
        "apostrofo tipografico + em dash",
    ),
    (
        "E6",
        "Salaire CHF 6’500 – 8’000 par mois",
        6500,
        8000,
        "CHF",
        "mensual, apostrofo",
    ),
    # --- F. Cotas simples / abiertas ---
    ("F1", "ab CHF 95'000", 95000, 95000, "CHF", "desde"),
    ("F2", "bis zu CHF 130'000", 130000, 130000, "CHF", "hasta"),
    ("F3", "Circa €50k", 50000, 50000, "EUR", "k minuscula"),
    ("F4", "Up to £45,000 depending on experience", 45000, 45000, "GBP", "up to"),
    ("F5", "Competitive salary", None, None, None, "sin cifra"),
    ("F6", "Salary negotiable, DOE", None, None, None, "sin cifra"),
    # --- G. Prosa DE/FR suiza (G8/P3-6) ---
    (
        "G1",
        "Wir bieten einen Jahreslohn zwischen CHF 95'000 und CHF 115'000 (13 Monatslöhne).",
        95000,
        115000,
        "CHF",
        "zwischen..und",
    ),
    (
        "G2",
        "Salaire annuel: entre CHF 90'000 et CHF 110'000",
        90000,
        110000,
        "CHF",
        "entre..et",
    ),
    (
        "G3",
        "Lohnklasse 18 - 22 gemäss kantonalem Reglement, CHF 92'000 - CHF 118'000",
        92000,
        118000,
        "CHF",
        "Lohnklasse",
    ),
    (
        "G4",
        "Besoldungsklasse 12, Jahresgehalt CHF 88'000 bis CHF 104'000",
        88000,
        104000,
        "CHF",
        "bis como separador",
    ),
    ("G5", "Stufe 3 - 5, CHF 85'000 - CHF 95'000", 85000, 95000, "CHF", "Stufe"),
    # --- H. Periodos ---
    ("H1", "CHF 45 - CHF 60 pro Stunde", 45, 60, "CHF", "por hora"),
    ("H2", "£120 - £160 per day", 120, 160, "GBP", "por dia"),
    ("H3", "CHF 7'500 - CHF 9'200 pro Monat", 7500, 9200, "CHF", "mensual"),
    ("H4", "80k - 100k CHF", 80000, 100000, "CHF", "shorthand k a los dos lados"),
    ("H5", "80 - 100k CHF", 80000, 100000, "CHF", "k solo a la derecha (G2/P2-3)"),
    # --- I. Trampas de magnitud pequeña (filas REALES del corpus vivo) ---
    ("I1", "12-42508 EUR", 12, 42508, "EUR", "fila REAL (control)"),
    ("I2", "720-2400 EUR", 720, 2400, "EUR", "fila REAL (control)"),
    ("I3", "21-42508 EUR", 21, 42508, "EUR", "fila REAL (control)"),
    # --- J. Formas mixtas y sucias ---
    (
        "J1",
        "Salary: 45,000 - 55,000 EUR + 10% bonus",
        45000,
        55000,
        "EUR",
        "bonus en % detras",
    ),
    ("J2", "€45,000 – €55,000 (pro rata for 0.6 FTE)", 45000, 55000, "EUR", "FTE"),
    (
        "J3",
        "CHF 100'000 (100%) - CHF 60'000 (60%)",
        100000,
        60000,
        "CHF",
        "rango invertido por pensum",
    ),
    (
        "J4",
        "2 positions: 1x 100% and 1x 50%. CHF 95'000 - CHF 105'000",
        95000,
        105000,
        "CHF",
        "conteo de plazas",
    ),
    ("J5", "Salary band 4 to 6 / €48,000 to €58,000", 48000, 58000, "EUR", "band..to"),
    (
        "J6",
        "On-call allowance CHF 250 - CHF 400 per week; base CHF 95'000 - CHF 110'000",
        250,
        400,
        "CHF",
        "complemento PRIMERO",
    ),
]

# Lo que el parser de HOY no acierta, con su razón. `strict=True`: si alguno
# empieza a pasar, la suite lo marca como fallo para que el ciclo que lo
# arregle lo retire de aquí y lo declare.
COTAS_CONOCIDAS = {
    "A7": "cota aparcada G5/P3-5: sin rango que casar, `single` es leftmost y "
    "se queda con el numero de escala de dos digitos (12)",
    "A8": "la escala mete el importe entre parentesis y `to` une los DOS "
    "parentesis, no los dos importes",
    "D10": "cota declarada del ancla lexica (G8/P2-1): un `plain` en medio de "
    "la prosa y sin palabra de sueldo delante pierde ante un candidato "
    "con divisa, aunque ese candidato sea una glosa entre parentesis",
    "J3": "el rango va invertido por pensum y el parentesis de porcentaje corta "
    "el patron; `normalize_salary` haria el swap, pero no llega",
}


def _param(caso):
    """Las cotas conocidas entran como `xfail(strict=True)` — un `pytest.xfail()`
    imperativo cortocircuita el test y JAMÁS avisaría de un XPASS, que es
    justamente la señal que queremos."""
    marks = (
        [pytest.mark.xfail(reason=COTAS_CONOCIDAS[caso[0]], strict=True)]
        if caso[0] in COTAS_CONOCIDAS
        else []
    )
    return pytest.param(caso, marks=marks, id=caso[0])


@pytest.mark.parametrize("caso", [_param(c) for c in CORPUS_PROSA])
def test_corpus_prosa(caso):
    """El parser dice lo que el anuncio dice, sobre prosa realista."""
    cid, texto, emin, emax, ecur, nota = caso
    lo, hi, cur = DataNormalizer._parse_salary_string(texto)
    assert (lo, hi, cur) == (
        None if emin is None else float(emin),
        None if emax is None else float(emax),
        ecur,
    ), f"{cid} ({nota}): {texto!r}"


# Las NUEVE formas de la familia que G8/P2-1 encontró: ruido de CUATRO cifras
# —un año, un número de referencia, un id de vacante, un recuento— que la
# guarda de MAGNITUD deja pasar porque su cota baja es >= 1000, delante de un
# rango anclado por divisa en los dos extremos. Sin el ancla léxica de
# `_ANCLA_SUELDO_RE`, las nueve devuelven el ruido.
FAMILIA_RUIDO_CUATRO_CIFRAS = [
    ("Ref. 2024 - 1187. Salary 80,000 - CHF 100,000 per annum", 80000, 100000),
    ("Job ID 4521-9987 | Gehalt: CHF 95'000 - CHF 115'000", 95000, 115000),
    ("Réf. 2025-0043 — Salaire CHF 92'000 - CHF 108'000", 92000, 108000),
    ("Fixed term 2026 - 2027. Salary £34,000 - £41,000 per annum", 34000, 41000),
    ("Vertrag 2026 - 2028, Jahreslohn CHF 95'000 - CHF 115'000", 95000, 115000),
    ("Vacancy 1000 - 1200 hours per year, £34,000 - £41,000", 34000, 41000),
    ("Reg. No. 1998-2004, salary CHF 90'000 - CHF 120'000", 90000, 120000),
    ("Established 1897 - 2024. Salary: CHF 95'000 - CHF 115'000", 95000, 115000),
    ("Roll 1,100 - 1,300 pupils. £34,000 - £41,000 per annum", 34000, 41000),
]


@pytest.mark.parametrize("texto,emin,emax", FAMILIA_RUIDO_CUATRO_CIFRAS)
def test_familia_ruido_de_cuatro_cifras(texto, emin, emax):
    """MORDIDA de G8/P2-1: sin el ancla léxica las nueve devuelven el ruido."""
    lo, hi, _cur = DataNormalizer._parse_salary_string(texto)
    assert (lo, hi) == (float(emin), float(emax)), texto


def test_referencia_no_se_persiste_como_salario_minimo():
    """La firma exacta de la familia «~2000x»: `Réf. 2025-0043` daba
    `(2025, 43)` y el swap de `normalize_salary` lo dejaba en
    `salary_min_chf = 43`, `salary_max_chf = 2025`."""
    datos = DataNormalizer.normalize_salary(
        {"salary_original": "Réf. 2025-0043 — Salaire CHF 92'000 - CHF 108'000"}
    )
    assert datos["salary_min_chf"] == 92000
    assert datos["salary_max_chf"] == 108000


# G8/P3-6: las formas canónicas DE/FR del mercado suizo, que hasta G8 caían al
# camino `single` porque el separador solo conocía el guion y `to`.
@pytest.mark.parametrize(
    "texto,emin,emax",
    [
        ("Besoldungsklasse 12, Jahresgehalt CHF 88'000 bis CHF 104'000", 88000, 104000),
        (
            "Wir bieten einen Jahreslohn zwischen CHF 95'000 und CHF 115'000",
            95000,
            115000,
        ),
        ("Salaire annuel: entre CHF 90'000 et CHF 110'000", 90000, 110000),
        ("Stipendio annuo CHF 85'000 a CHF 95'000", 85000, 95000),
        ("Salaire de CHF 80'000 à CHF 100'000", 80000, 100000),
    ],
)
def test_separadores_de_rango_de_y_fr(texto, emin, emax):
    """MORDIDA de G8/P3-6. El primero es corrupción ×7.000: sin `bis` como
    separador, `_SALARY_SINGLE_RE` es leftmost y persiste la clase salarial
    (12 CHF anuales) en vez del sueldo."""
    lo, hi, _cur = DataNormalizer._parse_salary_string(texto)
    assert (lo, hi) == (float(emin), float(emax)), texto


def test_las_tres_filas_reales_del_corpus_vivo_siguen_intactas():
    """Control: la cota baja pequeña de estas tres ES el salario. Exigirle a
    `plain` las pruebas SIEMPRE (y no solo cuando hay otro candidato) las
    rompe."""
    for texto, emin, emax in (
        ("12-42508 EUR", 12, 42508),
        ("21-42508 EUR", 21, 42508),
        ("720-2400 EUR", 720, 2400),
    ):
        lo, hi, cur = DataNormalizer._parse_salary_string(texto)
        assert (lo, hi, cur) == (float(emin), float(emax), "EUR"), texto

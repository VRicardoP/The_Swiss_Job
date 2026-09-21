"""Search metadata preserved from the previous WorkingNomads producer.

Pure boundary rules, not a new classifier. Keep substring priority and the
200-character description window: changing them changes saved-search results.
The standalone contract test compares these tables with the retiring writer;
the core never imports its runtime or reads its database.
"""

SENIORITY_PATTERNS = [
    ("head", ["head of", "director", "directeur", "direktor", "chef de"]),
    ("lead", ["lead", "leiter", "team lead", "chef d'équipe", "teamleiter"]),
    ("senior", ["senior", "sr.", "experienced", "erfahren", "expérimenté"]),
    ("mid", ["mid-level", "mid level", "confirmé", "confirmed"]),
    ("junior", ["junior", "jr.", "anfänger", "débutant"]),
    ("intern", ["intern", "internship", "praktikant", "praktikum", "stage", "stagiaire",
                "trainee", "werkstudent", "abschlussarbeit", "bachelorarbeit", "masterarbeit",
                "diplomarbeit", "studienarbeit", "praxissemester", "pflichtpraktikum"]),
]

CONTRACT_PATTERNS = [
    ("apprenticeship", ["apprenticeship", "apprentissage", "lehre", "lehrstelle", "lehrling",
                       "berufslehre", "ausbildungsplatz"]),
    ("internship", ["internship", "praktikum", "stage", "stagiaire", "trainee", "werkstudent",
                    "abschlussarbeit", "bachelorarbeit", "masterarbeit", "pflichtpraktikum",
                    "praxissemester"]),
    ("temporary", ["temporary", "temp ", "temporär", "intérim", "interim"]),
    ("contract", ["contract", "freelance", "befristet", "cdd", "contrat à durée déterminée"]),
    ("part_time", ["part-time", "part time", "teilzeit", "temps partiel",
                   "50%", "60%", "70%", "80%", "90%"]),
    ("full_time", ["full-time", "full time", "100%", "vollzeit", "temps plein",
                   "festanstellung", "unbefristet", "cdi", "permanent"]),
]

SWISS_CANTONS = {
    "zurich": "ZH", "zürich": "ZH", "zh": "ZH",
    "bern": "BE", "berne": "BE", "be": "BE",
    "luzern": "LU", "lucerne": "LU", "lu": "LU",
    "uri": "UR", "ur": "UR", "schwyz": "SZ", "sz": "SZ",
    "obwalden": "OW", "ow": "OW", "nidwalden": "NW", "nw": "NW",
    "glarus": "GL", "gl": "GL", "zug": "ZG", "zg": "ZG",
    "fribourg": "FR", "freiburg": "FR", "solothurn": "SO", "so": "SO",
    "basel-stadt": "BS", "basel": "BS", "bs": "BS", "bâle": "BS",
    "basel-landschaft": "BL", "bl": "BL", "schaffhausen": "SH", "sh": "SH",
    "appenzell ausserrhoden": "AR", "ar": "AR", "appenzell innerrhoden": "AI",
    "st. gallen": "SG", "st.gallen": "SG", "sg": "SG", "saint-gall": "SG",
    "graubünden": "GR", "graubunden": "GR", "grisons": "GR", "gr": "GR",
    "aargau": "AG", "argovie": "AG", "ag": "AG",
    "thurgau": "TG", "thurgovie": "TG", "tg": "TG",
    "ticino": "TI", "tessin": "TI", "ti": "TI",
    "vaud": "VD", "waadt": "VD", "vd": "VD",
    "valais": "VS", "wallis": "VS", "vs": "VS",
    "neuchâtel": "NE", "neuchatel": "NE", "neuenburg": "NE", "ne": "NE",
    "genève": "GE", "geneve": "GE", "geneva": "GE", "genf": "GE", "ge": "GE",
    "jura": "JU", "ju": "JU",
}


def _first_match(text, patterns):
    return next((value for value, words in patterns if any(word in text for word in words)), None)


def swiss_canton(text):
    """Canton code of a free-text location, or None. Mirrors `utils.text.extract_canton`.

    Whole-string match first, then substring on names longer than two characters
    — a bare 'be' or 'ag' inside a word must never invent a canton. Returning
    None is the honest answer: the retiring writers leave the column NULL.
    """
    text = text.lower().strip() if isinstance(text, str) else ""
    if not text:
        return None
    return SWISS_CANTONS.get(text) or next(
        (code for name, code in SWISS_CANTONS.items()
         if len(name) > 2 and name in text), None)


def workingnomads_metadata(title, description, location):
    title = title.lower() if isinstance(title, str) else ""
    description = description[:200].lower() if isinstance(description, str) else ""
    location = location.lower().strip() if isinstance(location, str) else ""
    canton = swiss_canton(location)
    return {"canton": canton, "language": "en",
            "seniority": _first_match(title, SENIORITY_PATTERNS),
            "contract_type": _first_match(" " + title + " " + description, CONTRACT_PATTERNS)}

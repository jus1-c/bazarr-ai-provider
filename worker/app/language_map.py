from __future__ import annotations


SUBSOURCE_LANGUAGE_BY_ALPHA3 = {
    "ara": "arabic",
    "ben": "bengali",
    "bul": "bulgarian",
    "cat": "catalan",
    "ces": "czech",
    "dan": "danish",
    "deu": "german",
    "ell": "greek",
    "eng": "english",
    "est": "estonian",
    "fas": "persian",
    "fin": "finnish",
    "fra": "french",
    "heb": "hebrew",
    "hin": "hindi",
    "hrv": "croatian",
    "hun": "hungarian",
    "ind": "indonesian",
    "ita": "italian",
    "jpn": "japanese",
    "kor": "korean",
    "msa": "malay",
    "nld": "dutch",
    "nor": "norwegian",
    "pol": "polish",
    "por": "portuguese",
    "ron": "romanian",
    "rus": "russian",
    "slk": "slovak",
    "slv": "slovenian",
    "spa": "spanish",
    "srp": "serbian",
    "swe": "swedish",
    "tha": "thai",
    "tur": "turkish",
    "ukr": "ukrainian",
    "vie": "vietnamese",
    "zho": "chinese",
}


def to_subsource_language(alpha3: str | None) -> str | None:
    if not alpha3:
        return None
    return SUBSOURCE_LANGUAGE_BY_ALPHA3.get(alpha3.lower())

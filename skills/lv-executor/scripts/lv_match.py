"""Czyste dopasowanie nazw drużyn do kafli bukmacherów.

Ten moduł nie dotyka przeglądarki ani API — dzięki temu reguły dopasowania
można uruchomić w testach jednostkowych. `lv-place.py` wstrzykuje go do
programu harnessu przez `LV_SKILL_DIR`.
"""

from __future__ import annotations

import re
import unicodedata

# ø, æ, ß, đ, œ, ı NIE rozkładają się w NFD na literę + znak diakrytyczny —
# zostawały w tokenie i psuły wyszukiwarkę („Bodø/Glimt” dawało „bod”).
NORDIC_TRANSLATE = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "å": "a",
        "Å": "A",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
        "ß": "ss",
        "đ": "d",
        "Đ": "D",
        "œ": "oe",
        "Œ": "OE",
        "ı": "i",
    }
)


def norm(s: object) -> str:
    """Lowercase, bez diakrytyków, z rozłożeniem nordyckich ligatur."""
    text = str(s or "").translate(NORDIC_TRANSLATE)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.lower().replace("ł", "l").strip()


# Prefiksy i sufiksy klubowe, które nie pomagają w wyszukiwaniu. `acf` brakowało
# do 2026-11-08: „ACF Fiorentina” dawało zapytanie „acf”, a STS nie ma takiego
# kafla — stąd `event_not_found` i wyczerpane próby AKO.
GENERIC = {
    "fc",
    "cf",
    "sc",
    "ac",
    "afc",
    "acf",
    "cfc",
    "cff",
    "bk",
    "ks",
    "mks",
    "gks",
    "rks",
    "club",
    "clube",
    "sport",
    "sporting",
    "athletic",
    "atletico",
    "united",
    "city",
    "town",
    "real",
    "de",
    "la",
    "el",
    "the",
    "kobiety",
    "women",
    "balompie",
    "tc",
    "sk",
    "fk",
    "ii",
    "b",
    "u19",
    "u21",
    "u23",
    "ii.",
    "calcio",
    "futbol",
    "fussball",
    "football",
    "sv",
    "vfb",
    "vfl",
    "tsg",
    "ssc",
    "as",
    "us",
    "ss",
    "rc",
    "rcd",
    "cd",
    "ud",
    "sd",
    "ca",
    "if",
    "ik",
    "nk",
    "hnk",
    "osc",
    "ogc",
    "stade",
    "olympique",
    "racing",
    "deportivo",
    "utd",
}

# Nazwy w bazie są oryginalne (FC Bayern München, FC Internazionale Milano), STS
# pisze po polsku (Bayern Monachium, Inter Mediolan), Superbet po niemiecku lub
# angielsku. Aliasy dotyczą miasta/klubu, nie prefiksu („ACF” usuwa GENERIC).
ALIAS_GROUPS = [
    {"bodo", "bodoe"},
    {"madrid", "madryt"},
    {"munchen", "munich", "monachium"},
    {"milano", "milan", "mediolan"},
    {"internazionale", "inter"},
    {"torino", "turin", "turyn"},
    {"roma", "rome", "rzym"},
    {"napoli", "naples", "neapol"},
    {"koln", "cologne", "kolonia"},
    {"bremen", "brema"},
    {"nurnberg", "nuremberg", "norymberga"},
    {"hannover", "hanower"},
    {"lisboa", "lisbon", "lizbona"},
    {"praha", "prague", "praga"},
    {"wien", "vienna", "wieden"},
    {"london", "londyn"},
    {"kobenhavn", "copenhagen", "kopenhaga"},
    {"moskva", "moscow", "moskwa"},
    {"beograd", "belgrade", "belgrad"},
    {"bucuresti", "bucharest", "bukareszt"},
    {"brugge", "bruges", "brugia"},
    {"venezia", "venice", "wenecja"},
    {"firenze", "florence", "florencja"},
    {"genova", "genoa", "genua"},
    {"sevilla", "seville", "sewilla"},
    {"mallorca", "majorka"},
    {"athens", "ateny"},
    {"kyiv", "kiev", "kijow"},
    {"warszawa", "warsaw"},
    {"krakow", "cracow"},
    {"marseille", "marsylia"},
    {"paris", "paryz"},
    {"zurich", "zurych"},
    {"geneve", "geneva", "genewa"},
    {"basel", "bazylea"},
    {"antwerpen", "antwerp", "antwerpia"},
    {"dinamo", "dynamo"},
    {"monchengladbach", "gladbach"},
    {"nottingham", "nottm"},
    # STS pisze „Rennes”, baza „Stade Rennais FC 1901”.
    {"rennais", "rennes"},
]
ALIASES = {token: group for group in ALIAS_GROUPS for token in group}


def words_of(text: object) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", norm(text)))


def key_tokens(team: object) -> list[str]:
    """Tokeny identyfikujące klub: bez prefiksów typu FC/ACF/CFC i bez cyfr."""
    raw = [token for token in re.split(r"[^a-z0-9]+", norm(team)) if token]
    tokens = [token for token in raw if len(token) >= 3 and token not in GENERIC and not token.isdigit()]
    if not tokens:
        tokens = [token for token in raw if len(token) >= 2 and not token.isdigit()]
    return tokens


def variants(token: str) -> set[str]:
    return ALIASES.get(token, {token})


def team_position(text: object, team: object) -> int:
    """Indeks pierwszego charakterystycznego słowa drużyny; -1 = brak."""
    tokens = key_tokens(team)
    if not tokens:
        return -1
    haystack = norm(text)
    best = -1
    for variant in variants(tokens[0]):
        match = re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(variant), haystack)
        if match and (best < 0 or match.start() < best):
            best = match.start()
    return best


def text_has_team(text: object, team: object) -> bool:
    return team_position(text, team) >= 0


# Drużyny młodzieżowe, rezerwy, kobiety: kafel z takim znacznikiem pasuje po
# nazwach do meczu seniorów („warszawa” + „krakow” → Escola Varsovia U19).
SQUAD_MARKERS = {
    "u16",
    "u17",
    "u18",
    "u19",
    "u20",
    "u21",
    "u22",
    "u23",
    "ii",
    "iii",
    "kobiety",
    "women",
    "ladies",
    "rezerwy",
}


def squad_markers(text: object) -> set[str]:
    tokens = set(re.split(r"[^a-z0-9]+", norm(text)))
    found = tokens & SQUAD_MARKERS
    if "[k]" in norm(text) or "(k)" in norm(text):
        found.add("kobiety")
    return found


def team_coverage(text: object, home: object, away: object) -> int:
    """Ile słów kluczowych OBU drużyn jest w tekście; 0 = brak dopasowania.

    Świadomie wymaga kolejności gospodarz → gość: kafel „Juventus – Fiorentina”
    nie może zostać uznany za zlecenie „Fiorentina – Juventus” bez sprawdzenia,
    czy to na pewno ten sam mecz. Wyjątki (mecze rezerw/kobiet) odrzuca
    `squad_markers`.
    """
    home_pos = team_position(text, home)
    away_pos = team_position(text, away)
    if home_pos < 0 or away_pos < 0:
        return 0
    if home_pos > away_pos and key_tokens(home)[:1] != key_tokens(away)[:1]:
        return 0
    if squad_markers(text) - squad_markers(f"{home} {away}"):
        return 0
    words = words_of(text)
    return sum(1 for token in key_tokens(home) + key_tokens(away) if variants(token) & words)


def search_queries(home: object, away: object) -> list[str]:
    """Zapytania do wyszukiwarki: pierwsze tokeny z aliasami, potem najdłuższe.

    Wyszukiwarki buków nie lubią pełnych nazw („RCD Espanyol” daje pustkę,
    „espanyol” działa), dlatego nie wysyłamy całej nazwy.
    """
    queries: list[str] = []
    for team in (home, away):
        tokens = key_tokens(team)
        if tokens:
            for value in [tokens[0]] + sorted(variants(tokens[0]) - {tokens[0]}):
                if value not in queries:
                    queries.append(value)
    for team in (home, away):
        tokens = sorted(key_tokens(team), key=len, reverse=True)
        if tokens and tokens[0] not in queries:
            queries.append(tokens[0])
    return queries[:4] or [str(home)]


def pick_best_tile(
    tiles: list[dict],
    home: object,
    away: object,
    href_ok=None,
) -> tuple[str, int]:
    """Najlepszy kafel (href, score) wg pokrycia nazw OBU drużyn."""
    best_href = ""
    best_score = 0
    for tile in tiles or []:
        href = str(tile.get("href") or "")
        text = norm(str(tile.get("text") or "") + " " + href.replace("-", " "))
        if href_ok is not None and not href_ok(href, text):
            continue
        score = team_coverage(text, home, away)
        if score > best_score:
            best_href, best_score = href, score
    return best_href, best_score


def classify_search_failure(trace: list[dict]) -> tuple[str, str]:
    """Brak kafli = awaria wyszukiwarki (retry); kafle bez dopasowania = brak oferty.

    To rozróżnienie jest ważne: `event_not_found` retryuje cały kupon trzy razy,
    a `bookmaker_not_offering` zamyka zlecenie od razu jako świadome pominięcie.
    """
    has_tiles = any(int(item.get("tiles") or 0) > 0 for item in trace or [])
    if has_tiles:
        return "skipped", "bookmaker_not_offering"
    return "failed", "event_not_found"


def search_trace_summary(trace: list[dict]) -> str:
    """Krótki, logowalny ślad wyszukiwania: zapytanie → kafle/dopasowanie."""
    parts = []
    for item in trace or []:
        query = str(item.get("query") or "?")
        tiles = int(item.get("tiles") or 0)
        score = int(item.get("score") or 0)
        parts.append(f"{query}: {tiles} kafli, dopasowanie {score}")
    return "; ".join(parts) or "brak zapytań"

"""Brand + model parsing for the "Item (brand + model)" field.

Pure functions working on plain data, so the data migration can use exactly the same rules:

    match_key("Mp-1400")            -> "mp1400"   (lowercase, no spaces or punctuation)
    split_brand("xxio mp 1400", brands) -> brand "XXIO", rest "mp 1400"   (longest brand at the start wins)
"""
import re
from dataclasses import dataclass, field

_NON_KEY = re.compile(r"[\W_]+", re.UNICODE)

SEED_BRANDS = ["XXIO", "Titleist", "TaylorMade", "Callaway", "Ping", "Mizuno", "Srixon", "Cleveland", "Cobra",
               "Honma", "Yonex", "Bridgestone", "PXG", "Odyssey", "Scotty Cameron", "Wilson", "Majesty", "Romaro",
               "Fourteen"]


def match_key(text):
    """Lowercase, no spaces or punctuation: "Mp-1400", "mp 1400", "MP1400" -> "mp1400"."""
    return _NON_KEY.sub("", (text or "").lower())


def tidy(text):
    """Trimmed, single spaces, letters kept as typed."""
    return " ".join((text or "").split())


def split_brand(text, brands):
    """Longest known brand at the start of text, ignoring case/spaces/punctuation.

    brands: iterable of (match_key, display_name). Returns (display_name or None, rest of the text, tidied).
    """
    text = tidy(text)
    key = match_key(text)
    best = None
    for bkey, display in brands:
        if bkey and key.startswith(bkey) and (best is None or len(bkey) > len(best[0])):
            best = (bkey, display)
    if not best:
        return None, text
    # walk the original text until the brand's letters/digits are used up; the rest is the model
    need, i = len(best[0]), 0
    while i < len(text) and need:
        if match_key(text[i]):
            need -= len(match_key(text[i]))
        i += 1
    rest = text[i:].lstrip(" -_/.,·:;")
    return best[1], tidy(rest)


def edit_distance(a, b, limit=3):
    """Levenshtein distance (stops early above limit)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def closest(key, candidates, max_distance=2):
    """The candidate (match_key, display) nearest to key within max_distance, else None.
    Short words (4 letters or fewer) only get a suggestion for a 1-letter difference ("xxip" -> XXIO, not "Pro" -> PXG)."""
    if len(key) <= 4:
        max_distance = min(max_distance, 1)
    best = None
    for ckey, display in candidates:
        if not key or not ckey or ckey == key:
            continue
        d = edit_distance(key, ckey, max_distance)
        if d <= max_distance and (best is None or d < best[0]):
            best = (d, display)
    return best[1] if best else None


@dataclass
class Parsed:
    text: str = ""
    brand: str = ""              # Brand display name ("" = no brand)
    model: str = ""              # model display name (existing spelling, or as typed)
    model_is_new: bool = False
    unknown_brand: str = ""      # first word, when it isn't a known brand
    brand_guess: str = ""        # "Did you mean XXIO?"
    model_guess: str = ""        # "Did you mean MP1400?"
    suggestions: list = field(default_factory=list)

    @property
    def display(self):
        return tidy(f"{self.brand} {self.model}")


def parse(text, brands, models_for):
    """brands: [(key, display)]; models_for(brand_display) -> [(key, display)] for that brand."""
    text = tidy(text)
    out = Parsed(text=text)
    if not text:
        return out
    brand, rest = split_brand(text, brands)
    if brand is None:
        first = text.split()[0]
        out.unknown_brand = first
        out.brand_guess = closest(match_key(first), brands) or ""
        out.model = text                       # until someone says it's a brand
        out.model_is_new = True
        return out
    out.brand = brand
    if not rest:
        return out
    models = dict(models_for(brand))
    mkey = match_key(rest)
    if mkey in models:
        out.model = models[mkey]
    else:
        out.model, out.model_is_new = rest, True
        out.model_guess = closest(mkey, list(models.items())) or ""
    return out


def search(query, combos, limit=8):
    """Saved "Brand Model" combos where every typed word is found in the combo's key ("xxio 14" -> XXIO MP1400).

    combos: [(key, display)]."""
    words = [match_key(w) for w in tidy(query).split()]
    words = [w for w in words if w]
    if not words:
        return []
    hits = [display for key, display in combos if all(w in key for w in words)]
    return sorted(hits, key=lambda d: (len(d), d.lower()))[:limit]

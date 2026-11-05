"""Player / team name normalization for cross-book matching."""
from __future__ import annotations

import re
import unicodedata

# Common suffix / punctuation noise in sports props.
_SUFFIX_RE = re.compile(
    r"\b(jr\.?|sr\.?|ii|iii|iv|v)\b", re.IGNORECASE
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")

# Occasional nickname / spelling aliases seen across books.
_ALIASES = {
    "pj washington": "p j washington",
    "pj washington jr": "p j washington",
    "rj barrett": "r j barrett",
    "cj mccollum": "c j mccollum",
    "tj mcconnell": "t j mcconnell",
    "og anunoby": "o g anunoby",
    "deaaron fox": "de aaron fox",
    "lamelo ball": "la melo ball",
}


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def normalize_player(name: str) -> str:
    """Lowercase, strip accents/punct, drop Jr/Sr/II, collapse whitespace."""
    if not name:
        return ""
    s = strip_accents(name).lower().strip()
    s = s.replace("'", "").replace("’", "")
    s = _SUFFIX_RE.sub("", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return _ALIASES.get(s, s)


def names_match(a: str, b: str) -> bool:
    """Exact normalized match, or last-name + first-initial when unambiguous enough."""
    na, nb = normalize_player(a), normalize_player(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # "donovan mitchell" vs "d mitchell"
    pa, pb = na.split(), nb.split()
    if len(pa) >= 2 and len(pb) >= 2 and pa[-1] == pb[-1]:
        if pa[0][0] == pb[0][0] and (len(pa[0]) == 1 or len(pb[0]) == 1 or pa[0] == pb[0]):
            return True
    return False

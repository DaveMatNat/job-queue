"""Cross-source identity: canonical_key collapses the same job seen in several
repos into one queue row. Exact dedupe is the (source, source_id) primary key."""

import hashlib
import re

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_COMPANY_SUFFIXES = {"inc", "llc", "corp", "corporation", "co", "ltd", "company", "technologies", "labs"}
_TITLE_NOISE = {
    "intern", "internship", "20\\d\\d", "summer", "fall", "winter", "spring",
    "us", "usa", "co", "op", "coop",
}
_TITLE_NOISE_RE = re.compile(r"\b(" + "|".join(_TITLE_NOISE) + r")\b")


def norm_text(s: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", s.lower())).strip()


def norm_company(s: str) -> str:
    words = norm_text(s).split()
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def norm_title(s: str) -> str:
    return _WS.sub(" ", _TITLE_NOISE_RE.sub(" ", norm_text(s))).strip()


def canonical_key(company: str, title: str, locations: list[str]) -> str:
    loc = norm_text(locations[0]) if locations else ""
    basis = f"{norm_company(company)}|{norm_title(title)}|{loc}"
    return hashlib.sha256(basis.encode()).hexdigest()

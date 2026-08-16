"""RawListing -> Listing (canonical schema), plus deterministic knockout filters."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from intern_queue.sources.base import RawListing
from intern_queue.sources.speedyapply import age_to_hours


@dataclass
class Listing:
    company: str
    title: str
    locations: list[str]
    url: str
    apply_url: str
    posted_at: datetime | None
    season: str  # "" = unknown, kept and given the benefit of the doubt
    terms: list[str]
    sponsorship_note: str
    is_active: bool
    source: str
    source_id: str
    first_seen_at: datetime | None
    raw: dict = field(repr=False, default_factory=dict)
    role_class: str = "other"  # title-keyword heuristic; enrichment refines it
    resume_version: str = "swe"


_SEASON_IN_TITLE = re.compile(
    r"(?:(spring|summer|fall|winter)\s*[’']?(20\d\d|\d\d)\b)|(?:(20\d\d)\s*(spring|summer|fall|winter))", re.I
)
_ML_HINT = re.compile(r"machine learning|\bml\b|\bai\b|artificial intelligence|deep learning|research|data scien|\bnlp\b|computer vision|\bllm\b", re.I)


def detect_season(title: str) -> str:
    m = _SEASON_IN_TITLE.search(title)
    if not m:
        return ""
    season = (m.group(1) or m.group(4)).capitalize()
    year = m.group(2) or m.group(3)
    if len(year) == 2:
        year = "20" + year
    return f"{season} {year}"


def heuristic_role(title: str) -> tuple[str, str]:
    if _ML_HINT.search(title):
        return "ml", "ml"
    return "swe", "swe"


def to_listing(rl: RawListing, target_season: str) -> Listing:
    r = rl.raw
    role, resume = heuristic_role(r.get("title", ""))
    common = dict(source=rl.source, source_id=rl.source_id, raw=r, first_seen_at=None,
                  role_class=role, resume_version=resume)
    if rl.source in ("simplify", "vansh"):
        posted = datetime.fromtimestamp(r["date_posted"], tz=timezone.utc) if r.get("date_posted") else None
        if rl.source == "vansh":
            # season has no year; the repo is 2027-scoped (NOTES.md)
            season = f"{r['season']} 2027" if r.get("season") else ""
            terms = [season] if season else []
        else:
            terms = [t for t in r.get("terms", []) if t != "N/A"]
            season = target_season if target_season in terms else (terms[0] if terms else "")
        return Listing(
            company=r["company_name"], title=r["title"], locations=r.get("locations") or [],
            url=r.get("company_url") or r["url"], apply_url=r["url"], posted_at=posted,
            season=season, terms=terms, sponsorship_note=r.get("sponsorship", ""),
            is_active=bool(r.get("active", True)) and bool(r.get("is_visible", True)), **common,
        )
    # speedyapply: no active flag (rows are removed when closed), no absolute date
    hours = age_to_hours(r.get("age", ""))
    posted = datetime.now(timezone.utc) - timedelta(hours=hours) if hours is not None else None
    season = detect_season(r["title"])
    return Listing(
        company=r["company"], title=r["title"], locations=[r["location"]] if r.get("location") else [],
        url=r.get("company_url") or r["apply_url"], apply_url=r["apply_url"], posted_at=posted,
        season=season, terms=[season] if season else [], sponsorship_note="",
        is_active=True, **common,
    )


def knockout_reason(listing: Listing, config: dict) -> str | None:
    """Deterministic drop filters. Returns a reason string, or None to keep."""
    target = config["general"]["season"]
    if not listing.is_active:
        return "inactive"
    if listing.season and listing.season != target:
        return f"season:{listing.season}"
    if not listing.season and listing.source == "simplify":
        return "season:unknown"  # simplify tags seasons reliably; untagged rows are stale
    for pat in config["filters"]["exclude_title_patterns"]:
        if re.search(pat, listing.title, re.I):
            return f"title:{pat}"
    allowed = config["filters"]["allowed_locations"]
    if allowed and listing.locations:
        if not any(a.lower() in loc.lower() for a in allowed for loc in listing.locations):
            return f"location:{listing.locations[0]}"
    return None

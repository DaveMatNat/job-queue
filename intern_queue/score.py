"""Deterministic scoring: score = tier_weight * role_fit * recency * ats_friction.
No LLM anywhere in here. Every factor is recorded so `explain` can show its work."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from intern_queue.dedupe import norm_company


@dataclass
class Score:
    tier_weight: float
    tier_source: str  # matched company name, or "default"
    role_fit: float
    role_matches: list[tuple[str, float]] = field(default_factory=list)
    recency: float = 0.0
    age_days: float = 0.0
    ats: float = 0.0
    ats_reason: str = ""

    @property
    def total(self) -> float:
        return self.tier_weight * self.role_fit * self.recency * self.ats


def tier_map(config: dict) -> dict[str, tuple[str, float]]:
    return {norm_company(name): (name, float(w)) for name, w in config["companies"].items()}


def role_fit(title: str, config: dict) -> tuple[float, list[tuple[str, float]]]:
    cfg = config["scoring"]["role_fit"]
    fit, matches = cfg["base"], []
    for table in ("include", "exclude"):
        for pattern, weight in cfg[table].items():
            if re.search(pattern, title, re.I):
                fit += weight
                matches.append((pattern, weight))
    return max(0.05, min(1.0, fit)), matches


def ats_friction(apply_url: str, config: dict) -> tuple[float, str]:
    cfg = config["scoring"]["ats"]
    parsed = urlparse(apply_url or "")
    host = parsed.netloc.lower()
    for fragment, value in cfg.items():
        if fragment in ("custom", "unknown"):
            continue
        if fragment in host:
            return float(value), fragment
    if host.startswith(("careers.", "jobs.")) or "/careers" in parsed.path.lower():
        return float(cfg["custom"]), "custom career site"
    return float(cfg["unknown"]), "unknown host"


def recency(first_seen_at: str, config: dict, now: datetime) -> tuple[float, float]:
    seen = datetime.fromisoformat(first_seen_at)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - seen).total_seconds() / 86400)
    half_life = config["scoring"]["recency_half_life_days"]
    return max(config["scoring"]["recency_floor"], 0.5 ** (age_days / half_life)), age_days


def score_row(row, config: dict, now: datetime | None = None, tiers: dict | None = None) -> Score:
    """Score a listings DB row. Pass `now` for determinism in tests."""
    now = now or datetime.now(timezone.utc)
    tiers = tiers if tiers is not None else tier_map(config)
    matched = tiers.get(norm_company(row["company"]))
    tier_source, tier = matched if matched else ("default", config["scoring"]["default_tier"])
    fit, matches = role_fit(row["title"], config)
    rec, age = recency(row["first_seen_at"], config, now)
    ats, ats_reason = ats_friction(row["apply_url"], config)
    return Score(tier, tier_source, fit, matches, rec, age, ats, ats_reason)

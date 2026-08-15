from datetime import datetime, timedelta, timezone

from intern_queue.score import score_row, tier_map

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def row(company="Acme", title="Software Engineer Intern", first_seen=NOW,
        apply_url="https://boards.greenhouse.io/acme/jobs/1"):
    return {"company": company, "title": title, "apply_url": apply_url,
            "first_seen_at": first_seen.isoformat(timespec="seconds")}


def test_deterministic_given_fixed_now(config):
    a = score_row(row(), config, NOW)
    b = score_row(row(), config, NOW)
    assert a.total == b.total > 0


def test_tier_matching_is_normalized(config):
    hit = score_row(row(company="Google LLC"), config, NOW)
    miss = score_row(row(company="Unheard-Of Startup"), config, NOW)
    assert hit.tier_weight == 1.0 and hit.tier_source == "Google"
    assert miss.tier_weight == config["scoring"]["default_tier"] and miss.tier_source == "default"


def test_recency_dominates_fresh_beats_stale_tier1(config):
    """The most important property: a fresh posting at an unknown company must
    outrank a 6-day-old posting at a tier-1.0 company (same title, same ATS)."""
    fresh_unknown = score_row(row(company="Unknown Co", first_seen=NOW), config, NOW)
    stale_tier1 = score_row(row(company="Google", first_seen=NOW - timedelta(days=6)), config, NOW)
    assert fresh_unknown.total > stale_tier1.total


def test_recency_half_life_and_floor(config):
    day0 = score_row(row(first_seen=NOW), config, NOW)
    day3 = score_row(row(first_seen=NOW - timedelta(days=3)), config, NOW)
    day60 = score_row(row(first_seen=NOW - timedelta(days=60)), config, NOW)
    assert abs(day0.recency - 1.0) < 1e-9
    assert abs(day3.recency - 0.5) < 1e-9
    assert day60.recency == config["scoring"]["recency_floor"]


def test_ats_friction_tiers(config):
    gh = score_row(row(apply_url="https://boards.greenhouse.io/x/jobs/1"), config, NOW)
    wd = score_row(row(apply_url="https://acme.wd5.myworkdayjobs.com/careers/job/1"), config, NOW)
    custom = score_row(row(apply_url="https://careers.acme.com/jobs/1"), config, NOW)
    unknown = score_row(row(apply_url="https://apply.example.io/1"), config, NOW)
    assert gh.ats == 1.0
    assert wd.ats == 0.35
    assert custom.ats == 0.6
    assert unknown.ats == 0.5
    assert gh.total > custom.total > wd.total


def test_role_fit_include_and_exclude(config):
    ml = score_row(row(title="Machine Learning Engineer Intern"), config, NOW)
    qa = score_row(row(title="Software Test Engineer Intern"), config, NOW)
    assert ml.role_fit > qa.role_fit
    assert any(w < 0 for _, w in qa.role_matches)


def test_every_factor_is_explainable(config):
    s = score_row(row(company="Nvidia", title="Machine Learning Intern"), config, NOW)
    assert s.tier_source == "Nvidia"
    assert s.role_matches  # matched patterns recorded for `explain`
    assert s.ats_reason
    expected = s.tier_weight * s.role_fit * s.recency * s.ats
    assert abs(s.total - expected) < 1e-12


def test_tier_map_reusable(config):
    tiers = tier_map(config)
    s = score_row(row(company="Databricks"), config, NOW, tiers)
    assert s.tier_weight == 0.9

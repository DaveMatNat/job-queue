from intern_queue.normalize import detect_season, knockout_reason, to_listing
from intern_queue.sources.base import RawListing
from intern_queue.sources.speedyapply import age_to_hours, parse_markdown


def test_simplify_normalization(simplify_raws, config):
    listings = [to_listing(r, "Summer 2027") for r in simplify_raws]
    assert all(l.company and l.title and l.apply_url for l in listings)
    kept = [l for l in listings if knockout_reason(l, config) is None]
    assert kept, "fixture must contain keepable Summer 2027 listings"
    assert all(l.season == "Summer 2027" for l in kept)
    assert all(l.posted_at is not None for l in kept)


def test_vansh_season_gets_year(vansh_raws):
    listings = [to_listing(r, "Summer 2027") for r in vansh_raws]
    assert any(l.season == "Summer 2027" for l in listings)
    assert all(" " in l.season for l in listings if l.season)  # never a bare "Summer"


def test_knockout_inactive(simplify_raws, config):
    inactive = [r for r in simplify_raws if not r.raw["active"]]
    assert inactive, "fixture must contain inactive records"
    for r in inactive:
        assert knockout_reason(to_listing(r, "Summer 2027"), config) == "inactive"


def test_knockout_wrong_season(simplify_raws, config):
    wrong = [r for r in simplify_raws
             if r.raw["active"] and r.raw["is_visible"] and r.raw["terms"] == ["Summer 2026"]]
    assert wrong, "fixture must contain wrong-season records"
    for r in wrong:
        assert knockout_reason(to_listing(r, "Summer 2027"), config) == "season:Summer 2026"


def test_knockout_title_exclude(config):
    rl = RawListing("vansh", "x", {"company_name": "Acme", "title": "Product Manager Intern",
                                   "active": True, "is_visible": True, "season": "Summer",
                                   "date_posted": 0, "url": "https://x", "locations": []})
    assert knockout_reason(to_listing(rl, "Summer 2027"), config).startswith("title:")


def test_knockout_location_allowlist(config):
    config["filters"]["allowed_locations"] = ["NY", "Remote"]
    base = {"company_name": "Acme", "title": "Software Engineer Intern", "active": True,
            "is_visible": True, "season": "Summer", "date_posted": 0, "url": "https://x"}
    ny = to_listing(RawListing("vansh", "a", base | {"locations": ["New York, NY"]}), "Summer 2027")
    tx = to_listing(RawListing("vansh", "b", base | {"locations": ["Austin, TX"]}), "Summer 2027")
    unknown = to_listing(RawListing("vansh", "c", base | {"locations": []}), "Summer 2027")
    assert knockout_reason(ny, config) is None
    assert knockout_reason(tx, config) == "location:Austin, TX"
    assert knockout_reason(unknown, config) is None  # unknown location: benefit of the doubt


def test_detect_season():
    assert detect_season("Software Engineer Intern - Summer 2027") == "Summer 2027"
    assert detect_season("Fullstack Intern - 2027 Summer") == "Summer 2027"
    assert detect_season("SWE Intern - Fall 2026") == "Fall 2026"
    assert detect_season("Software Engineer Intern") == ""


def test_speedyapply_parse_all_sections(speedyapply_md):
    rows = parse_markdown(speedyapply_md)
    assert len(rows) == 12  # 4 per section, including the salary-less OTHER table
    assert {r["section"] for r in rows} == {"FAANG", "QUANT", "OTHER"}
    assert all(r["company"] and r["title"] and r["apply_url"].startswith("http") for r in rows)
    assert all("+" not in r["location"] for r in rows)  # "+N" suffix stripped


def test_age_to_hours():
    assert age_to_hours("3d") == 72
    assert age_to_hours("12h") == 12
    assert age_to_hours("2w") == 336
    assert age_to_hours("1mo") == 720
    assert age_to_hours("fresh") is None

"""End-to-end pipeline (normalize -> knockout -> dedupe -> upsert) with no network."""

from intern_queue.candidate import referral_companies
from intern_queue.cli import ingest
from intern_queue.sources.base import RawListing


def simplify_raw(sid, company, title, loc="Mountain View, CA", active=True, terms=("Summer 2027",)):
    return RawListing("simplify", sid, {
        "id": sid, "company_name": company, "title": title, "active": active,
        "is_visible": True, "terms": list(terms), "date_posted": 1755000000,
        "date_updated": 1755000000, "url": f"https://boards.greenhouse.io/{sid}",
        "locations": [loc], "company_url": "", "sponsorship": "Other",
    })


def test_ingest_fixture_batch(con, config, simplify_raws):
    counts, new_ids, drops, holds = ingest(con, simplify_raws, config, set())
    # the fixture happens to contain an intra-source duplicate, which links
    assert sum(counts.values()) == len(simplify_raws)
    assert counts["new"] == len(new_ids) > 0
    assert drops["inactive"] >= 3 and drops["season"] >= 3  # fixture plants these
    dropped_rows = con.execute("SELECT reason FROM drops").fetchall()
    assert len(dropped_rows) == counts["dropped"]  # every drop is logged with a reason


def test_second_ingest_is_all_seen(con, config, simplify_raws):
    first, *_ = ingest(con, simplify_raws, config, set())
    second, new_ids, _, _ = ingest(con, simplify_raws, config, set())
    assert second["new"] == 0 and not new_ids
    assert second["seen"] == first["new"] + first["linked"]


def test_cross_source_collapse_shows_all_sources(con, config):
    a = simplify_raw("s1", "Google LLC", "Software Engineer Intern - Summer 2027")
    b = RawListing("vansh", "v1", {
        "company_name": "Google", "title": "Software Engineer Intern", "active": True,
        "is_visible": True, "season": "Summer", "date_posted": 1755000000,
        "url": "https://careers.google.com/x", "locations": ["Mountain View, CA"],
        "sponsorship": "Other",
    })
    c = RawListing("speedyapply_swe", "sa1", {
        "company": "Google", "title": "Software Engineer - Intern - US",
        "location": "Mountain View, CA", "apply_url": "https://careers.google.com/y",
        "company_url": "", "age": "2d", "section": "FAANG",
    })
    for batch in ([a], [b], [c]):
        ingest(con, batch, config, set())
    listings = con.execute("SELECT id FROM listings").fetchall()
    assert len(listings) == 1  # one row...
    sources = {r["source"] for r in con.execute("SELECT source FROM source_records")}
    assert sources == {"simplify", "vansh", "speedyapply_swe"}  # ...three source records


def test_different_roles_do_not_collapse(con, config):
    ingest(con, [simplify_raw("s1", "Google", "Software Engineer Intern"),
                 simplify_raw("s2", "Google", "Machine Learning Engineer Intern")], config, set())
    assert con.execute("SELECT count(*) n FROM listings").fetchone()["n"] == 2


def test_referral_hold_flagged_and_pinned(con, config, candidate):
    referrals = referral_companies(candidate)
    assert referrals == {"google"}  # have_referral only; "requested" is not a hold
    counts, new_ids, _, holds = ingest(
        con, [simplify_raw("s1", "Google LLC", "Software Engineer Intern"),
              simplify_raw("s2", "Some Startup", "Software Engineer Intern", loc="NYC")],
        config, referrals)
    assert counts["new"] == 2 and len(holds) == 1
    assert holds[0].company == "Google LLC"
    rows = {r["company"]: r["referral_hold"] for r in con.execute("SELECT company, referral_hold FROM listings")}
    assert rows == {"Google LLC": 1, "Some Startup": 0}


def test_deactivation_of_known_listing(con, config):
    ingest(con, [simplify_raw("s1", "Acme", "Software Engineer Intern")], config, set())
    ingest(con, [simplify_raw("s1", "Acme", "Software Engineer Intern", active=False)], config, set())
    row = con.execute("SELECT is_active FROM listings").fetchone()
    assert row["is_active"] == 0  # closed at the source -> leaves the queue

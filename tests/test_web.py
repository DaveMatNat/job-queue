"""Web-layer logic, exercised directly against an in-memory DB — no sockets."""

from intern_queue.candidate import referral_companies
from intern_queue.cli import ingest
from intern_queue.web import do_action, listings_payload, stats_payload
from tests.test_ingest import simplify_raw


def seed(con, config, candidate):
    ingest(con, [
        simplify_raw("g1", "Google LLC", "Software Engineer Intern"),
        simplify_raw("a1", "Acme", "Machine Learning Intern", loc="New York, NY"),
        simplify_raw("b1", "Beta Corp", "Software Engineer Intern", loc="Austin, TX"),
    ], config, referral_companies(candidate))


def test_listings_payload_pins_holds_and_scores(con, config, candidate):
    seed(con, config, candidate)
    data = listings_payload(con, config, ["NEW", "QUEUED"])
    assert len(data["listings"]) == 3
    top = data["listings"][0]
    assert top["company"] == "Google LLC" and top["referral_hold"] is True
    rest = [i["score"]["total"] for i in data["listings"][1:]]
    assert rest == sorted(rest, reverse=True)
    assert data["meta"]["season"] == config["general"]["season"]
    assert {"total", "tier", "fit", "recency", "ats", "ats_reason"} <= set(top["score"])


def test_apply_action_refuses_referral_hold_without_force(con, config, candidate):
    seed(con, config, candidate)
    gid = con.execute("SELECT id FROM listings WHERE referral_hold=1").fetchone()["id"]
    code, payload = do_action(con, {"id": gid, "action": "apply"})
    assert code == 409 and payload["error"] == "referral_hold"
    assert con.execute("SELECT status FROM listings WHERE id=?", (gid,)).fetchone()["status"] == "NEW"
    code, payload = do_action(con, {"id": gid, "action": "apply", "force": True})
    assert code == 200 and payload["to"] == "APPLIED"
    # the same guard covers the generic status route
    con.execute("UPDATE listings SET status='NEW' WHERE id=?", (gid,))
    code, _ = do_action(con, {"id": gid, "action": "status", "state": "APPLIED"})
    assert code == 409


def test_action_validation(con, config, candidate):
    seed(con, config, candidate)
    assert do_action(con, {"id": 99999, "action": "apply"})[0] == 404
    lid = con.execute("SELECT id FROM listings WHERE referral_hold=0").fetchone()["id"]
    assert do_action(con, {"id": lid, "action": "status", "state": "BANANA"})[0] == 400
    assert do_action(con, {"id": lid, "action": "dance"})[0] == 400
    code, payload = do_action(con, {"id": lid, "action": "skip", "reason": "meh"})
    assert code == 200 and payload["to"] == "SKIPPED"


def test_stats_payload_shape(con, config, candidate):
    seed(con, config, candidate)
    lid = con.execute("SELECT id FROM listings WHERE referral_hold=0").fetchone()["id"]
    do_action(con, {"id": lid, "action": "apply"})
    do_action(con, {"id": lid, "action": "status", "state": "OA"})
    s = stats_payload(con, config)
    assert s["applied"] == 1 and s["responded"] == 1
    assert s["by_status"]["OA"] == 1
    assert s["fresh_72h"] == 2  # the two still-queued listings, seeded just now
    assert len(s["weeks"]) == 1
    assert "season" in s and "last_poll" in s

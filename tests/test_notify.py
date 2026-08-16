import json
from datetime import datetime, timedelta, timezone

import httpx

from intern_queue import db, notify
from intern_queue.score import Score


def hit(total):
    row = {"company": "Acme", "title": "SWE Intern", "referral_hold": 0}
    s = Score(1.0, "Acme", total, [], 1.0, 0.0, 1.0, "test")
    return row, s


def notify_config(config, **over):
    config["notify"].update({"enabled": True, "ntfy_url": "https://ntfy.test/topic"} | over)
    return config


def make_client(sent):
    return httpx.Client(transport=httpx.MockTransport(
        lambda req: (sent.append(req.read().decode()), httpx.Response(200))[1]))


def test_below_threshold_never_stages(con, config):
    notify.queue_hits(con, notify_config(config), [hit(0.2)])
    assert db.meta_get(con, "pending_notify") is None


def test_digest_sent_once_then_rate_limited(con, config):
    config, sent = notify_config(config), []
    client = make_client(sent)
    notify.queue_hits(con, config, [hit(0.9)])
    assert notify.flush(con, config, client) is True
    assert "Acme" in sent[0]
    notify.queue_hits(con, config, [hit(0.8)])  # arrives inside the cooldown window
    assert notify.flush(con, config, client) is False
    assert len(sent) == 1
    assert len(json.loads(db.meta_get(con, "pending_notify"))) == 1  # rolled over, not lost


def test_rollover_flushes_after_window(con, config):
    config, sent = notify_config(config), []
    client = make_client(sent)
    notify.queue_hits(con, config, [hit(0.9)])
    notify.flush(con, config, client)
    notify.queue_hits(con, config, [hit(0.8)])
    stale = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.meta_set(con, "last_notify_at", stale.isoformat(timespec="seconds"))
    assert notify.flush(con, config, client) is True
    assert len(sent) == 2


def test_disabled_never_posts(con, config):
    sent = []
    notify.queue_hits(con, notify_config(config, enabled=False), [hit(0.9)])
    assert notify.flush(con, config, make_client(sent)) is False
    assert sent == []

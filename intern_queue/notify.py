"""ntfy.sh notifications — feature-flagged off by default, rate-limited to one
digest per window. Hits arriving during the cooldown roll over to the next flush."""

import json
from datetime import datetime, timezone

import httpx

from intern_queue import db
from intern_queue.sources.base import USER_AGENT


def queue_hits(con, config: dict, hits: list[tuple]) -> None:
    """Stage (row, score) pairs that cleared the notify threshold."""
    lines = [
        f"{s.total:.2f}  {r['company']} — {r['title'][:70]}" + ("  [REFERRAL HOLD]" if r["referral_hold"] else "")
        for r, s in hits
        if s.total >= config["notify"]["min_score"]
    ]
    if lines:
        pending = json.loads(db.meta_get(con, "pending_notify") or "[]")
        db.meta_set(con, "pending_notify", json.dumps(pending + lines))


def flush(con, config: dict, client: httpx.Client | None = None) -> bool:
    """Send one digest if anything is pending and the rate-limit window allows."""
    cfg = config["notify"]
    if not cfg["enabled"] or not cfg["ntfy_url"]:
        return False
    pending = json.loads(db.meta_get(con, "pending_notify") or "[]")
    if not pending:
        return False
    now = datetime.now(timezone.utc)
    last = db.meta_get(con, "last_notify_at")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < cfg["digest_interval_minutes"] * 60:
        return False
    body = f"{len(pending)} high-score listing(s):\n" + "\n".join(pending[:15])
    if len(pending) > 15:
        body += f"\n… and {len(pending) - 15} more (run: intern-queue queue)"
    own_client = client is None
    client = client or httpx.Client(timeout=15)
    try:
        client.post(cfg["ntfy_url"], content=body.encode(),
                    headers={"Title": "intern-queue", "User-Agent": USER_AGENT}).raise_for_status()
    finally:
        if own_client:
            client.close()
    db.meta_set(con, "pending_notify", "[]")
    db.meta_set(con, "last_notify_at", now.isoformat(timespec="seconds"))
    return True

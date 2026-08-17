"""Local web UI — stdlib HTTP server over the same DB and scoring code the CLI
uses. Binds 127.0.0.1 only; no framework, no build step, one packaged HTML file.
Referral-hold safety is enforced server-side, same as the CLI."""

import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from io import StringIO
from urllib.parse import parse_qs, urlparse

from rich.console import Console

from intern_queue import db, render
from intern_queue.score import score_row, tier_map

_poll_lock = threading.Lock()
QUEUE_STATUSES = ("NEW", "QUEUED")


def listings_payload(con, config, statuses: list[str]) -> dict:
    from intern_queue.cli import fetch_queue_rows

    tiers, now = tier_map(config), datetime.now(timezone.utc)
    if set(statuses) <= set(QUEUE_STATUSES):
        rows = fetch_queue_rows(con, tuple(statuses))
    else:  # past-queue tabs keep listings even after the source deactivates them
        marks = ",".join("?" * len(statuses))
        rows = con.execute(
            f"SELECT l.*, group_concat(DISTINCT sr.source) AS sources FROM listings l"
            f" JOIN source_records sr ON sr.listing_id = l.id"
            f" WHERE l.status IN ({marks}) GROUP BY l.id", statuses).fetchall()
    items = []
    for r in rows:
        s = score_row(r, config, now, tiers)
        items.append({
            "id": r["id"], "company": r["company"], "title": r["title"],
            "locations": json.loads(r["locations"]), "url": r["url"], "apply_url": r["apply_url"],
            "status": r["status"], "referral_hold": bool(r["referral_hold"]),
            "resume": r["resume_version"], "role_class": r["role_class"],
            "sources": (r["sources"] or "").split(","), "season": r["season"],
            "sponsorship": r["sponsorship_note"], "first_seen_at": r["first_seen_at"],
            "enriched": bool(r["enriched_at"]), "note": r["note"],
            "knockouts": json.loads(r["knockouts"] or "[]"),
            "escalations": json.loads(r["escalations"] or "[]"),
            "score": {"total": round(s.total, 4), "tier": s.tier_weight, "tier_source": s.tier_source,
                      "fit": round(s.role_fit, 3), "matches": s.role_matches,
                      "recency": round(s.recency, 4), "age_days": round(s.age_days, 2),
                      "ats": s.ats, "ats_reason": s.ats_reason},
        })
    items.sort(key=lambda i: (i["referral_hold"], i["score"]["total"]), reverse=True)
    last = con.execute("SELECT max(fetched_at) t FROM fetch_cache").fetchone()["t"]
    return {"meta": {"season": config["general"]["season"], "last_poll": last}, "listings": items}


def stats_payload(con, config) -> dict:
    from intern_queue.dedupe import norm_company

    by_status = {r["status"]: r["n"] for r in
                 con.execute("SELECT status, count(*) n FROM listings GROUP BY status")}
    weeks = [{"week": r["wk"], "n": r["n"]} for r in con.execute(
        "SELECT strftime('%Y-W%W', at) wk, count(*) n FROM transitions"
        " WHERE to_status='APPLIED' GROUP BY wk ORDER BY wk")]
    applied_ids = [r["listing_id"] for r in con.execute(
        "SELECT DISTINCT listing_id FROM transitions WHERE to_status='APPLIED'")]
    by_tier, by_source, responded = {}, {}, 0
    if applied_ids:
        marks = ",".join("?" * len(applied_ids))
        tiers = tier_map(config)
        for r in con.execute(f"SELECT company FROM listings WHERE id IN ({marks})", applied_ids):
            matched = tiers.get(norm_company(r["company"]))
            key = f"{matched[1]:.2f}" if matched else "default"
            by_tier[key] = by_tier.get(key, 0) + 1
        for r in con.execute(f"SELECT DISTINCT source, listing_id FROM source_records"
                             f" WHERE listing_id IN ({marks})", applied_ids):
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        responded = con.execute(
            f"SELECT count(DISTINCT listing_id) n FROM transitions WHERE listing_id IN ({marks})"
            " AND to_status IN ('OA','INTERVIEW','OFFER','REJECTED')", applied_ids).fetchone()["n"]
    drops = {}
    for r in con.execute("SELECT reason, count(*) n FROM drops GROUP BY reason"):
        key = r["reason"].split(":")[0]
        drops[key] = drops.get(key, 0) + r["n"]
    fresh = con.execute(
        "SELECT count(*) n FROM listings WHERE status IN ('NEW','QUEUED') AND is_active=1"
        " AND first_seen_at >= datetime('now', '-3 days')").fetchone()["n"]
    last = con.execute("SELECT max(fetched_at) t FROM fetch_cache").fetchone()["t"]
    from intern_queue.enrich import pending_count, preflight

    blockers = preflight()
    return {"by_status": by_status, "weeks": weeks, "by_tier": by_tier, "by_source": by_source,
            "applied": len(applied_ids), "responded": responded, "drops": drops, "fresh_72h": fresh,
            "season": config["general"]["season"], "last_poll": last,
            "enrich": {"ready": not blockers, "blockers": blockers, "pending": pending_count(con)}}


def do_action(con, body: dict) -> tuple[int, dict]:
    lid, action = int(body.get("id", 0)), body.get("action")
    row = con.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if row is None:
        return 404, {"error": f"no listing {lid}"}
    if action == "apply":
        state, reason = "APPLIED", None
    elif action == "skip":
        state, reason = "SKIPPED", body.get("reason") or None
    elif action == "status":
        state, reason = str(body.get("state", "")).upper(), body.get("reason") or None
        if state not in db.STATUSES:
            return 400, {"error": f"state must be one of {', '.join(db.STATUSES)}"}
    else:
        return 400, {"error": "action must be apply | skip | status"}
    if state == "APPLIED" and row["referral_hold"] and not body.get("force"):
        return 409, {"error": "referral_hold", "message": render.REFERRAL_WARNING,
                     "company": row["company"]}
    prev = db.set_status(con, lid, state, reason)
    return 200, {"id": lid, "from": prev, "to": state}


def run_poll(config, candidate, con) -> tuple[int, dict]:
    from intern_queue.cli import notify_new, poll_all

    if not _poll_lock.acquire(blocking=False):
        return 429, {"error": "a poll is already running"}
    try:
        results, new_ids, drop_reasons = poll_all(config, candidate, con)
        notify_new(con, config, new_ids)
        return 200, {"results": results, "new": len(new_ids), "drops": dict(drop_reasons)}
    finally:
        _poll_lock.release()


def run_enrich(config, candidate, con) -> tuple[int, dict]:
    """One enrichment batch. Setup problems return 400 with an actionable
    message; API/network failures return 502 rather than a bare traceback."""
    from intern_queue.enrich import run_enrichment

    out = Console(file=StringIO(), width=200)
    try:
        run_enrichment(con, config, candidate, out, batches=1)
    except SystemExit as e:  # preflight blockers and validation failures
        return 400, {"error": str(e)}
    except Exception as e:  # auth rejected, rate limited, network down
        return 502, {"error": f"{type(e).__name__}: {e}"}
    return 200, {"message": out.file.getvalue().strip() or "done"}


class Handler(BaseHTTPRequestHandler):
    config: dict = {}
    candidate: dict = {}

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _con(self):
        return db.connect(self.config["general"]["db_path"])

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                html = resources.files("intern_queue").joinpath("web.html").read_bytes()
                self._send(200, html, "text/html; charset=utf-8")
            elif parsed.path == "/api/listings":
                statuses = [s for s in parse_qs(parsed.query).get("statuses", ["NEW,QUEUED"])[0].split(",")
                            if s in db.STATUSES]
                con = self._con()
                try:
                    self._send(200, listings_payload(con, self.config, statuses or ["NEW", "QUEUED"]))
                finally:
                    con.close()
            elif parsed.path == "/api/stats":
                con = self._con()
                try:
                    self._send(200, stats_payload(con, self.config))
                finally:
                    con.close()
            elif parsed.path == "/api/export.md":
                from intern_queue.cli import fetch_queue_rows

                con = self._con()
                try:
                    now, tiers = datetime.now(timezone.utc), tier_map(self.config)
                    scored = [(r, score_row(r, self.config, now, tiers), r["sources"])
                              for r in fetch_queue_rows(con)]
                    scored.sort(key=lambda t: (t[0]["referral_hold"], t[1].total), reverse=True)
                    self._send(200, render.queue_markdown(scored).encode(), "text/markdown; charset=utf-8")
                finally:
                    con.close()
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # surface errors to the UI instead of a dead request
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid JSON body"})
        try:
            con = self._con()
            try:
                if self.path == "/api/poll":
                    code, payload = run_poll(self.config, self.candidate, con)
                elif self.path == "/api/action":
                    code, payload = do_action(con, body)
                elif self.path == "/api/enrich":
                    code, payload = run_enrich(self.config, self.candidate, con)
                else:
                    code, payload = 404, {"error": "not found"}
            finally:
                con.close()
            self._send(code, payload)
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def serve(config: dict, candidate: dict, port: int = 8777, open_browser: bool = True) -> None:
    Handler.config, Handler.candidate = config, candidate
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"intern-queue web UI on {url} — ctrl-c to stop")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")

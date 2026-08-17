"""intern-queue CLI. Detects, dedupes, ranks, and stages. You press Submit."""

import json
import re
import time
import tomllib
from collections import Counter
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from intern_queue import db, notify, render
from intern_queue.candidate import load_candidate, referral_companies
from intern_queue.dedupe import canonical_key, norm_company
from intern_queue.normalize import knockout_reason, to_listing
from intern_queue.score import score_row, tier_map
from intern_queue.sources import ALL_SOURCES

app = typer.Typer(add_completion=False, help=__doc__, no_args_is_help=True)
console = Console()
err = Console(stderr=True)

MIN_POLL_SECONDS = 600  # never hit the sources faster than every 10 minutes


def load_config() -> dict:
    path = Path("config.toml")
    if not path.exists():
        err.print("[red]no config.toml here — run `intern-queue init` first[/red]")
        raise typer.Exit(1)
    with path.open("rb") as f:
        return tomllib.load(f)


def open_session():
    config = load_config()
    candidate = load_candidate(config["general"]["candidate_file"])
    return config, candidate, db.connect(config["general"]["db_path"])


def fetch_queue_rows(con, statuses=("NEW", "QUEUED")):
    marks = ",".join("?" * len(statuses))
    return con.execute(
        f"SELECT l.*, group_concat(DISTINCT sr.source) AS sources FROM listings l"
        f" JOIN source_records sr ON sr.listing_id = l.id"
        f" WHERE l.status IN ({marks}) AND l.is_active = 1 GROUP BY l.id",
        statuses,
    ).fetchall()


def _transition(con, listing_id: int, state: str, reason: str | None = None) -> str:
    try:
        return db.set_status(con, listing_id, state, reason)
    except KeyError:
        err.print(f"[red]no listing {listing_id}[/red]")
        raise typer.Exit(1)


@app.command()
def init():
    """Create the DB and write a default config.toml (seeded from candidate.yaml tiers)."""
    template = resources.files("intern_queue").joinpath("config.toml").read_text()
    if not Path("candidate.yaml").exists() and Path("candidate.example.yaml").exists():
        Path("candidate.yaml").write_text(Path("candidate.example.yaml").read_text())
        err.print("[yellow]candidate.yaml was missing — copied candidate.example.yaml. Fill it in.[/yellow]")
    if Path("candidate.yaml").exists():
        candidate = load_candidate("candidate.yaml")
        tiers = candidate.get("tiers") or {}
        if tiers.get("companies"):
            entries = "".join(f'"{name}" = {float(w)}\n' for name, w in tiers["companies"].items())
            template = re.sub(r"^\[companies\]\n.*?\n\n\[notify\]", f"[companies]\n{entries}\n[notify]",
                              template, flags=re.S | re.M)
        if "default" in tiers:
            template = re.sub(r"default_tier = [\d.]+", f"default_tier = {float(tiers['default'])}", template)
    if Path("config.toml").exists():
        err.print("config.toml already exists — leaving it alone")
    else:
        Path("config.toml").write_text(template)
        console.print("wrote config.toml")
    config = load_config()
    db.connect(config["general"]["db_path"]).close()
    console.print(f"created {config['general']['db_path']} — next: intern-queue poll")


def ingest(con, raws, config, referrals) -> tuple[Counter, list[int], Counter, list]:
    """Normalize, knockout-filter, and upsert a batch of RawListings.
    Returns (outcome counts, new listing ids, drop reasons, new referral holds)."""
    counts, new_ids, drop_reasons, holds = Counter(), [], Counter(), []
    for rl in raws:
        listing = to_listing(rl, config["general"]["season"])
        reason = knockout_reason(listing, config)
        if reason:
            db.record_drop(con, rl.source, rl.source_id, listing.company, listing.title, reason)
            if reason == "inactive":
                db.deactivate_if_known(con, rl.source, rl.source_id)
            drop_reasons[reason.split(":")[0]] += 1
            counts["dropped"] += 1
            continue
        hold = norm_company(listing.company) in referrals
        outcome, lid = db.upsert(con, listing, canonical_key(
            listing.company, listing.title, listing.locations), hold)
        counts[outcome] += 1
        if outcome == "new":
            new_ids.append(lid)
            if hold:
                holds.append(listing)
    con.commit()
    return counts, new_ids, drop_reasons, holds


def poll_all(config, candidate, con) -> tuple[list[dict], list[int], Counter]:
    """Fetch and ingest every source (shared by CLI poll and the web UI).
    Returns (per-source report dicts, new listing ids, drop reasons)."""
    referrals = referral_companies(candidate)
    results, new_ids, drop_reasons = [], [], Counter()
    with httpx.Client(timeout=60) as client:
        for name, fetch in ALL_SOURCES:
            try:
                raws = fetch(client, con)
            except Exception as e:  # one dead source must not kill the run
                results.append({"source": name, "result": f"error: {type(e).__name__}: {e}",
                                "counts": {}, "holds": []})
                continue
            if raws is None:
                results.append({"source": name, "result": "304 not modified", "counts": {}, "holds": []})
                continue
            counts, ids, drops, holds = ingest(con, raws, config, referrals)
            new_ids += ids
            drop_reasons += drops
            results.append({"source": name, "result": f"{len(raws)} listings", "counts": dict(counts),
                            "holds": [f"{l.company}: {l.title}" for l in holds]})
    return results, new_ids, drop_reasons


def notify_new(con, config, new_ids: list[int]) -> None:
    if new_ids:
        tiers = tier_map(config)
        rows = con.execute(
            f"SELECT * FROM listings WHERE id IN ({','.join('?' * len(new_ids))})", new_ids).fetchall()
        notify.queue_hits(con, config, [(r, score_row(r, config, tiers=tiers)) for r in rows])
    notify.flush(con, config)


def poll_once(config, candidate, con) -> int:
    """Fetch every source, upsert, notify. Returns the number of new listings."""
    results, new_ids, drop_reasons = poll_all(config, candidate, con)
    report = Table(title=f"poll @ {db.now_iso()}")
    for col in ("source", "result", "new", "linked", "seen", "dropped"):
        report.add_column(col)
    for r in results:
        c = r["counts"]
        report.add_row(r["source"], r["result"],
                       *(str(c.get(k, 0)) for k in ("new", "linked", "seen", "dropped")))
        for hold in r["holds"]:
            err.print(Panel(f"{hold}\n{render.REFERRAL_WARNING}",
                            style="bold red", title="NEW REFERRAL_HOLD LISTING"))
    console.print(report)
    if drop_reasons:
        console.print("drops this run: " + ", ".join(f"{k}={v}" for k, v in drop_reasons.most_common())
                      + "  (details in the `drops` table)")
    console.print(f"[bold]{len(new_ids)} new listing(s)[/bold]")
    notify_new(con, config, new_ids)
    return len(new_ids)


@app.command()
def poll():
    """Fetch all sources (conditionally), upsert, and report what changed."""
    poll_once(*open_session())


@app.command()
def queue(top: int = typer.Option(25, "--top"), md: bool = typer.Option(False, "--md")):
    """Ranked queue of NEW/QUEUED listings. REFERRAL_HOLD rows pin to the top."""
    config, _, con = open_session()
    tiers, now = tier_map(config), datetime.now(timezone.utc)
    scored = [(r, score_row(r, config, now, tiers), r["sources"]) for r in fetch_queue_rows(con)]
    scored.sort(key=lambda t: (t[0]["referral_hold"], t[1].total), reverse=True)
    scored = scored[:top]
    if md:
        print(render.queue_markdown(scored))
    else:
        render.queue_table(scored, console)


@app.command()
def explain(listing_id: int):
    """Print every scoring factor and its contribution for one listing."""
    config, _, con = open_session()
    row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    if row is None:
        err.print(f"[red]no listing {listing_id}[/red]")
        raise typer.Exit(1)
    sources = con.execute(
        "SELECT group_concat(DISTINCT source) AS s FROM source_records WHERE listing_id=?",
        (listing_id,)).fetchone()["s"]
    render.explain_view(row, score_row(row, config), sources or "?", console)


@app.command()
def apply(listing_id: int, force: bool = typer.Option(False, "--force", help="override a referral hold")):
    """Mark a listing APPLIED (you actually submitted it yourself)."""
    _, _, con = open_session()
    row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    if row is None:
        err.print(f"[red]no listing {listing_id}[/red]")
        raise typer.Exit(1)
    if row["referral_hold"] and not force:
        err.print(Panel(f"{row['company']}: {row['title']}\n{render.REFERRAL_WARNING}\n\n"
                        "If you really applied through the referral (not the cold link), "
                        "re-run with --force.", style="bold red", title="REFUSING TO MARK APPLIED"))
        raise typer.Exit(1)
    prev = db.set_status(con, listing_id, "APPLIED")
    console.print(f"[{listing_id}] {row['company']} — {row['title']}: {prev} → APPLIED")


@app.command()
def skip(listing_id: int, reason: str = typer.Option("", "--reason")):
    """Mark a listing SKIPPED, with an optional reason for the record."""
    _, _, con = open_session()
    prev = _transition(con, listing_id, "SKIPPED", reason or None)
    console.print(f"[{listing_id}]: {prev} → SKIPPED" + (f" ({reason})" if reason else ""))


@app.command()
def status(listing_id: int, state: str):
    """Record a pipeline transition: QUEUED, OA, INTERVIEW, OFFER, REJECTED…"""
    state = state.upper()
    if state not in db.STATUSES:
        err.print(f"[red]state must be one of: {', '.join(db.STATUSES)}[/red]")
        raise typer.Exit(1)
    _, _, con = open_session()
    prev = _transition(con, listing_id, state)
    console.print(f"[{listing_id}]: {prev} → {state}")


@app.command()
def stats():
    """Funnel numbers: apps/week, by tier, by source, response rate."""
    config, _, con = open_session()
    by_status = con.execute("SELECT status, count(*) n FROM listings GROUP BY status").fetchall()
    console.print("pipeline: " + "  ".join(f"{r['status']}={r['n']}" for r in by_status))
    weeks = con.execute("SELECT strftime('%Y-W%W', at) wk, count(*) n FROM transitions"
                        " WHERE to_status='APPLIED' GROUP BY wk ORDER BY wk").fetchall()
    console.print("applications/week: " + ("  ".join(f"{r['wk']}: {r['n']}" for r in weeks) or "none yet"))
    applied = con.execute("SELECT DISTINCT listing_id FROM transitions WHERE to_status='APPLIED'").fetchall()
    applied_ids = [r["listing_id"] for r in applied]
    if applied_ids:
        marks = ",".join("?" * len(applied_ids))
        tiers = tier_map(config)
        tier_counts, src_counts = Counter(), Counter()
        for r in con.execute(f"SELECT * FROM listings WHERE id IN ({marks})", applied_ids):
            matched = tiers.get(norm_company(r["company"]))
            tier_counts[f"{matched[1]:.2f}" if matched else "default"] += 1
        for r in con.execute(f"SELECT DISTINCT source, listing_id FROM source_records"
                             f" WHERE listing_id IN ({marks})", applied_ids):
            src_counts[r["source"]] += 1
        responded = con.execute(
            f"SELECT count(DISTINCT listing_id) n FROM transitions WHERE listing_id IN ({marks})"
            " AND to_status IN ('OA','INTERVIEW','OFFER','REJECTED')", applied_ids).fetchone()["n"]
        console.print("applied by tier: " + "  ".join(f"{k}: {v}" for k, v in sorted(tier_counts.items(), reverse=True)))
        console.print("applied by source: " + "  ".join(f"{k}: {v}" for k, v in src_counts.most_common()))
        console.print(f"response rate: {responded}/{len(applied_ids)} "
                      f"({responded / len(applied_ids):.0%}) heard back (OA or later)")


@app.command()
def watch():
    """Poll on an interval (>= 10 min) forever; notify on high scorers."""
    config, candidate, con = open_session()
    interval = max(MIN_POLL_SECONDS, int(config["watch"]["interval_minutes"] * 60))
    console.print(f"watching every {interval // 60} min — ctrl-c to stop")
    try:
        while True:
            poll_once(config, candidate, con)
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("stopped")


@app.command()
def web(port: int = typer.Option(8777, "--port"),
        no_browser: bool = typer.Option(False, "--no-browser", help="don't open a browser tab")):
    """Serve the web UI on localhost — everything the CLI does, in one page."""
    from intern_queue.web import serve

    config, candidate, con = open_session()
    con.close()  # validated config + candidate + schema; the server opens per-request connections
    serve(config, candidate, port, open_browser=not no_browser)


@app.command()
def enrich(batches: int = typer.Option(1, "--batches", help="how many batches of 25 to run")):
    """Optional LLM pass: knockouts, role class, résumé version, escalations."""
    from intern_queue.enrich import run_enrichment

    config, candidate, con = open_session()
    run_enrichment(con, config, candidate, console, batches)


if __name__ == "__main__":
    app()

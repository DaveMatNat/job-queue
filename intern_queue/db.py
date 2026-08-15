"""SQLite persistence. No ORM — every query the tool runs is visible here."""

import json
import sqlite3
from datetime import datetime, timezone

STATUSES = ["NEW", "QUEUED", "APPLIED", "OA", "INTERVIEW", "OFFER", "REJECTED", "SKIPPED"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY,
  canonical_key TEXT UNIQUE NOT NULL,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  locations TEXT NOT NULL,              -- JSON array
  url TEXT,
  apply_url TEXT,
  posted_at TEXT,
  season TEXT,
  terms TEXT,                           -- JSON array
  sponsorship_note TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  first_seen_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'NEW',
  referral_hold INTEGER NOT NULL DEFAULT 0,
  role_class TEXT,
  resume_version TEXT,
  knockouts TEXT,                       -- JSON array (enrichment)
  escalations TEXT,                     -- JSON array (enrichment)
  note TEXT,
  enriched_at TEXT
);
CREATE TABLE IF NOT EXISTS source_records (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  raw TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY,
  listing_id INTEGER NOT NULL REFERENCES listings(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  at TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS fetch_cache (
  url TEXT PRIMARY KEY,
  etag TEXT,
  fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS drops (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  company TEXT,
  title TEXT,
  reason TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_etag(con, url: str) -> str | None:
    row = con.execute("SELECT etag FROM fetch_cache WHERE url=?", (url,)).fetchone()
    return row["etag"] if row else None


def set_etag(con, url: str, etag: str | None) -> None:
    con.execute(
        "INSERT INTO fetch_cache(url, etag, fetched_at) VALUES(?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET etag=excluded.etag, fetched_at=excluded.fetched_at",
        (url, etag, now_iso()),
    )
    con.commit()


def record_drop(con, source: str, source_id: str, company: str, title: str, reason: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO drops(source, source_id, company, title, reason, at) VALUES(?,?,?,?,?,?)",
        (source, source_id, company, title, reason, now_iso()),
    )


def set_status(con, listing_id: int, to_status: str, reason: str | None = None) -> str:
    """Transition a listing's status, logging it. Returns the previous status."""
    row = con.execute("SELECT status FROM listings WHERE id=?", (listing_id,)).fetchone()
    if row is None:
        raise KeyError(f"no listing with id {listing_id}")
    con.execute("UPDATE listings SET status=? WHERE id=?", (to_status, listing_id))
    con.execute(
        "INSERT INTO transitions(listing_id, from_status, to_status, at, reason) VALUES(?,?,?,?,?)",
        (listing_id, row["status"], to_status, now_iso(), reason),
    )
    con.commit()
    return row["status"]


def meta_get(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(con, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
    con.commit()


def upsert(con, listing, canonical_key: str, referral_hold: bool) -> tuple[str, int]:
    """Insert or refresh a listing. Returns (outcome, listing_id) where outcome is
    'new', 'linked' (new source for an existing listing), or 'seen' (same record again)."""
    now = now_iso()
    raw_json = json.dumps(listing.raw)
    existing = con.execute(
        "SELECT listing_id FROM source_records WHERE source=? AND source_id=?",
        (listing.source, listing.source_id),
    ).fetchone()
    if existing:
        con.execute(
            "UPDATE source_records SET raw=?, last_seen_at=? WHERE source=? AND source_id=?",
            (raw_json, now, listing.source, listing.source_id),
        )
        con.execute(
            "UPDATE listings SET is_active=?, apply_url=? WHERE id=?",
            (int(listing.is_active), listing.apply_url, existing["listing_id"]),
        )
        return "seen", existing["listing_id"]
    match = con.execute("SELECT id FROM listings WHERE canonical_key=?", (canonical_key,)).fetchone()
    if match:
        listing_id, outcome = match["id"], "linked"
    else:
        cur = con.execute(
            "INSERT INTO listings(canonical_key, company, title, locations, url, apply_url,"
            " posted_at, season, terms, sponsorship_note, is_active, first_seen_at,"
            " referral_hold, role_class, resume_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                canonical_key, listing.company, listing.title, json.dumps(listing.locations),
                listing.url, listing.apply_url,
                listing.posted_at.isoformat(timespec="seconds") if listing.posted_at else None,
                listing.season, json.dumps(listing.terms), listing.sponsorship_note,
                int(listing.is_active), now, int(referral_hold),
                listing.role_class, listing.resume_version,
            ),
        )
        listing_id, outcome = cur.lastrowid, "new"
    con.execute(
        "INSERT INTO source_records(source, source_id, listing_id, raw, first_seen_at, last_seen_at)"
        " VALUES(?,?,?,?,?,?)",
        (listing.source, listing.source_id, listing_id, raw_json, now, now),
    )
    return outcome, listing_id


def deactivate_if_known(con, source: str, source_id: str) -> None:
    """A listing we already track went inactive at its source — reflect that."""
    row = con.execute(
        "SELECT listing_id FROM source_records WHERE source=? AND source_id=?",
        (source, source_id),
    ).fetchone()
    if row:
        con.execute("UPDATE listings SET is_active=0 WHERE id=?", (row["listing_id"],))

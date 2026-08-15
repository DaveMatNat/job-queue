"""Source protocol and shared conditional-fetch logic."""

import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from intern_queue import db

USER_AGENT = "intern-queue/0.1 (personal internship-search tool for one student; conditional requests; not a crawler)"


@dataclass
class RawListing:
    source: str  # our tag: simplify | vansh | speedyapply_swe | speedyapply_ai
    source_id: str
    raw: dict


class Source(Protocol):
    NAME: str

    def fetch(self, client: httpx.Client, con) -> list[RawListing] | None:
        """Return listings, or None when the source responded 304 Not Modified."""


def conditional_get(client: httpx.Client, con, url: str, retries: int = 3) -> httpx.Response | None:
    """GET with If-None-Match from the fetch cache. Returns None on 304.
    Exponential backoff (2s/4s/8s) on 429 and 5xx; raises on final failure."""
    headers = {"User-Agent": USER_AGENT}
    etag = db.get_etag(con, url)
    if etag:
        headers["If-None-Match"] = etag
    delay = 2.0
    for attempt in range(retries + 1):
        resp = client.get(url, headers=headers, follow_redirects=True)
        if resp.status_code == 304:
            return None
        if resp.status_code == 200:
            db.set_etag(con, url, resp.headers.get("etag"))
            return resp
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries:
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
    resp.raise_for_status()  # unreachable, keeps type-checkers honest
    return None

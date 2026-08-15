"""SimplifyJobs/Summer2027-Internships — listings.json (see NOTES.md: file holds
every season; season filtering happens in normalize/knockouts, not here)."""

from intern_queue.sources.base import RawListing, conditional_get

NAME = "simplify"
URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"


def fetch(client, con) -> list[RawListing] | None:
    resp = conditional_get(client, con, URL)
    if resp is None:
        return None
    return [RawListing(NAME, r["id"], r) for r in resp.json()]

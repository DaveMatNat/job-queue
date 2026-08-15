"""vanshb03/Summer2027-Internships — listings.json (season field has no year;
the repo is 2027-scoped, so normalize maps "Summer" -> "Summer 2027")."""

from intern_queue.sources.base import RawListing, conditional_get

NAME = "vansh"
URL = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json"


def fetch(client, con) -> list[RawListing] | None:
    resp = conditional_get(client, con, URL)
    if resp is None:
        return None
    return [RawListing(NAME, r["id"], r) for r in resp.json()]

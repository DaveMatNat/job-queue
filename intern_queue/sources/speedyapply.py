"""speedyapply/2027-*-College-Jobs — no machine-readable file exists (tables are
generated from a private Supabase DB), so we parse the README markdown tables.
USA internships only; INTERN_INTL.md exists if that ever changes. See NOTES.md."""

import hashlib
import re

from intern_queue.sources.base import RawListing, conditional_get

URLS = {
    "speedyapply_swe": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "speedyapply_ai": "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md",
}

# the "Other" section uses bare TABLE_START/TABLE_END markers with no name
_SECTION = re.compile(r"<!-- TABLE(?:_([A-Z]+))?_START -->(.*?)<!-- TABLE(?:_[A-Z]+)?_END -->", re.S)
_STRONG = re.compile(r"<strong>(.*?)</strong>")
_HREF = re.compile(r'href="([^"]+)"')
_AGE = re.compile(r"(\d+)\s*(h|d|w|mo)\b")


def parse_markdown(text: str) -> list[dict]:
    rows = []
    for section, body in _SECTION.findall(text):
        for line in body.splitlines():
            cells = [c.strip() for c in line.split("|")]
            if len(cells) < 7 or "<a href" not in cells[1]:
                continue
            if len(cells) >= 8:  # FAANG/QUANT tables have a Salary column
                company_cell, title, location, salary, posting_cell, age = cells[1:7]
            else:  # the OTHER table has no Salary column
                company_cell, title, location, posting_cell, age = cells[1:6]
                salary = ""
            m = _STRONG.search(company_cell)
            href = _HREF.search(posting_cell)
            if not m or not href:
                continue
            company_href = _HREF.search(company_cell)
            rows.append({
                "company": m.group(1).strip(),
                "title": title,
                "location": re.sub(r"\s*\+\d+$", "", location),
                "salary": salary,
                "apply_url": href.group(1),
                "company_url": company_href.group(1) if company_href else "",
                "age": age,
                "section": section or "OTHER",
            })
    return rows


def age_to_hours(age: str) -> float | None:
    m = _AGE.search(age)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"h": 1, "d": 24, "w": 168, "mo": 720}[unit]


def make_fetch(name: str):
    url = URLS[name]

    def fetch(client, con) -> list[RawListing] | None:
        resp = conditional_get(client, con, url)
        if resp is None:
            return None
        out = []
        for row in parse_markdown(resp.text):
            # no stable ID in the table — the apply URL is the identity
            sid = hashlib.sha256(row["apply_url"].encode()).hexdigest()[:16]
            out.append(RawListing(name, sid, row))
        return out

    return fetch

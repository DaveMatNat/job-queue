import json
import tomllib
from pathlib import Path

import pytest

from intern_queue import db
from intern_queue.sources.base import RawListing

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def config():
    with open(Path(__file__).parents[1] / "intern_queue" / "config.toml", "rb") as f:
        return tomllib.load(f)


@pytest.fixture()
def con():
    con = db.connect(":memory:")
    yield con
    con.close()


@pytest.fixture()
def simplify_raws():
    data = json.loads((FIXTURES / "simplify_sample.json").read_text())
    return [RawListing("simplify", r["id"], r) for r in data]


@pytest.fixture()
def vansh_raws():
    data = json.loads((FIXTURES / "vansh_sample.json").read_text())
    return [RawListing("vansh", r["id"], r) for r in data]


@pytest.fixture()
def speedyapply_md():
    return (FIXTURES / "speedyapply_sample.md").read_text()


@pytest.fixture()
def candidate():
    return {
        "identity": {}, "education": {}, "eligibility": {}, "job_preferences": {},
        "application_preferences": {"auto_submit": False}, "documents": {},
        "approved_facts": {},
        "referrals": [{"company": "Google", "status": "have_referral"},
                      {"company": "Stripe", "status": "requested"}],
        "tiers": {"default": 0.5, "companies": {"Google": 1.0}},
    }

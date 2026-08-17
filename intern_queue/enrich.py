"""Phase 2: optional LLM enrichment. One call per batch of 25 — never per listing.
POLICY.md is the system prompt, verbatim; the only candidate data in context is
approved_facts + eligibility. The model classifies and flags; it never decides
whether to apply, and resume_version is re-derived from role_class in code."""

import json
import os
from pathlib import Path

import yaml

from intern_queue import db

ROLE_CLASSES = ["swe", "ml", "research", "quant", "infra", "other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["listings"],
    "properties": {
        "listings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "knockouts", "role_class", "resume_version", "escalations", "note"],
                "properties": {
                    "id": {"type": "integer"},
                    "knockouts": {"type": "array", "items": {"type": "string"}},
                    "role_class": {"type": "string", "enum": ROLE_CLASSES},
                    "resume_version": {"type": "string", "enum": ["swe", "ml"]},
                    "escalations": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        }
    },
}

TASK = """Enrich each posting below for the candidate described in the system prompt.
Per listing return: knockouts (HARD requirements only — stated-as-required graduation
window mismatch, citizenship/clearance, MS/PhD-only, non-Summer term; never missing
preferred qualifications), role_class, resume_version, escalations (questions whose
answer depends on a blank candidate field — output the question, never a guess), and
note (one sentence on fit, grounded only in approved_facts; if approved_facts is
empty, say the evidence is absent). Return every listing id exactly once.

Listings:
"""


def build_system(candidate: dict) -> str:
    policy = Path("POLICY.md").read_text()
    excerpt = yaml.safe_dump(
        {"approved_facts": candidate["approved_facts"], "eligibility": candidate["eligibility"]},
        sort_keys=True,
    )
    return f"{policy}\n\n# candidate.yaml excerpts (the only facts about the candidate)\n\n```yaml\n{excerpt}```"


def validate(data: dict, expected_ids: set[int]) -> list[dict]:
    """Structured outputs guarantee the shape; ids and completeness are on us.
    Fail loudly rather than silently dropping records."""
    items = data["listings"]
    got = [i["id"] for i in items]
    if sorted(got) != sorted(expected_ids):
        raise SystemExit(
            f"enrichment returned wrong ids — expected {sorted(expected_ids)}, got {sorted(got)}; "
            "nothing was written"
        )
    return items


def resume_for(role_class: str) -> str:
    return "ml" if role_class in ("ml", "research") else "swe"


def preflight() -> list[str]:
    """Everything that must be true before an enrichment call can work.
    Returns a list of human-readable blockers — empty means ready. Checked up
    front so both preconditions surface at once, instead of one error per fix."""
    blockers = []
    try:
        import anthropic  # noqa: F401
    except ImportError:
        blockers.append(
            "The anthropic SDK isn't installed. Run `uv sync` (it is a normal "
            "dependency now), then restart the server."
        )
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        blockers.append(
            "ANTHROPIC_API_KEY isn't set in the environment this process was started from. "
            "Set it and restart: export ANTHROPIC_API_KEY=sk-ant-... "
            "(get one at console.anthropic.com)"
        )
    return blockers


def pending_count(con) -> int:
    return con.execute(
        "SELECT count(*) n FROM listings WHERE enriched_at IS NULL AND is_active=1"
        " AND status IN ('NEW','QUEUED')").fetchone()["n"]


def run_enrichment(con, config, candidate, console, batches: int = 1) -> None:
    blockers = preflight()
    if blockers:
        raise SystemExit(" ".join(blockers))
    import anthropic

    client = anthropic.Anthropic()
    system = build_system(candidate)
    for _ in range(batches):
        rows = con.execute(
            "SELECT * FROM listings WHERE enriched_at IS NULL AND is_active=1"
            " AND status IN ('NEW','QUEUED') ORDER BY id LIMIT ?",
            (config["enrich"]["batch_size"],),
        ).fetchall()
        if not rows:
            console.print("nothing left to enrich")
            return
        payload = [
            {"id": r["id"], "company": r["company"], "title": r["title"],
             "locations": json.loads(r["locations"]), "season": r["season"],
             "sponsorship_note": r["sponsorship_note"], "apply_url": r["apply_url"]}
            for r in rows
        ]
        response = client.beta.messages.create(
            model=config["enrich"]["model"],
            max_tokens=16000,
            system=system,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": TASK + json.dumps(payload, indent=1)}],
        )
        if response.stop_reason == "refusal":
            raise SystemExit("enrichment request was refused by the model; nothing was written")
        text = next(b.text for b in response.content if b.type == "text")
        items = validate(json.loads(text), {r["id"] for r in rows})
        now = db.now_iso()
        for item in items:
            con.execute(
                "UPDATE listings SET role_class=?, resume_version=?, knockouts=?,"
                " escalations=?, note=?, enriched_at=? WHERE id=?",
                (item["role_class"], resume_for(item["role_class"]),
                 json.dumps(item["knockouts"]), json.dumps(item["escalations"]),
                 item["note"], now, item["id"]),
            )
        con.commit()
        flagged = sum(1 for i in items if i["knockouts"] or i["escalations"])
        console.print(f"enriched {len(items)} listings ({flagged} with knockouts/escalations)"
                      " — see `intern-queue explain <id>`")

# intern-queue

Detects, dedupes, ranks, and stages Summer 2027 internship postings from
community-maintained GitHub repos. **It never submits anything — you press
Submit yourself.** The edge it gives you is latency: applications in the first
24–48h convert dramatically better, so the tool polls cheaply (ETag conditional
requests), surfaces fresh postings first, and tells you which résumé to use.

## Sources

1. [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) (`listings.json`)
2. [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships) (`listings.json`)
3. [speedyapply/2027-SWE-College-Jobs](https://github.com/speedyapply/2027-SWE-College-Jobs) (README tables — no data file exists)
4. [speedyapply/2027-AI-College-Jobs](https://github.com/speedyapply/2027-AI-College-Jobs) (README tables)

Schema quirks are documented in [NOTES.md](NOTES.md) — these repos change shape
between seasons.

## Setup

```sh
uv sync                      # or: python -m venv .venv && pip install -e .
uv run intern-queue init     # creates DB + config.toml (+ candidate.yaml from the example)
$EDITOR candidate.yaml       # fill in your details — this file is gitignored, keep it that way
uv run intern-queue poll
uv run intern-queue queue
```

## Commands

```
intern-queue init                  # create db, write default config
intern-queue poll                  # fetch all sources (304s are free), upsert, report new
intern-queue queue [--top 25]      # ranked table; REFERRAL_HOLD rows pinned on top
intern-queue queue --md > queue.md # markdown with clickable apply links
intern-queue explain <id>          # score breakdown, factor by factor
intern-queue apply <id>            # mark APPLIED (refuses referral-hold rows without --force)
intern-queue skip <id> [--reason]  # mark SKIPPED
intern-queue status <id> <state>   # QUEUED / OA / INTERVIEW / OFFER / REJECTED
intern-queue stats                 # apps/week, by tier, by source, response rate
intern-queue watch                 # poll on an interval (>= 10 min), ntfy on tier-1 hits
intern-queue enrich                # optional LLM pass (needs ANTHROPIC_API_KEY)
```

## Scoring

`score = tier_weight × role_fit × recency × ats_friction` — deterministic, no
LLM. Recency has a 3-day half-life and dominates for fresh postings by design.
Every knob lives in `config.toml`; `explain` shows each factor's contribution so
you can tune without reading source.

## Referral safety

Companies with `status: have_referral` in `candidate.yaml` are flagged
`REFERRAL_HOLD` before they ever enter the queue: pinned to the top, cold apply
link suppressed in markdown output, and `apply` refuses without `--force`.
Applying cold to a role you have a referral for is irreversible — the tool's
job is to make that mistake hard.

## Enrichment (optional, Phase 2)

`intern-queue enrich` sends batches of 25 listings to Claude
(`claude-opus-5`, configurable) with [POLICY.md](POLICY.md) as the verbatim
system prompt and only `approved_facts` + `eligibility` from `candidate.yaml`
in context. It returns strict JSON (schema-enforced via structured outputs) with
hard-requirement knockouts, a role class, the résumé version, and escalation
questions it refuses to guess at. Server-side refusal fallbacks are enabled by
default. It never decides whether to apply and never writes résumé content.

## Tests

```sh
uv run pytest
```

Fixtures are ~50 real listings frozen from the live sources; no test opens a
network socket.

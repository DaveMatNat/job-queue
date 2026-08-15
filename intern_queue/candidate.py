"""Load and validate candidate.yaml — the single source of truth about David.
A blank value means unknown, never permission to infer. Malformed files fail loudly."""

from pathlib import Path

import yaml

REQUIRED_KEYS = {
    "identity": dict, "education": dict, "eligibility": dict, "job_preferences": dict,
    "application_preferences": dict, "documents": dict, "approved_facts": dict,
    "referrals": list, "tiers": dict,
}


def load_candidate(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"candidate file not found: {path} — it must exist (and stay out of git)")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise SystemExit(f"candidate.yaml is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise SystemExit("candidate.yaml must be a mapping at the top level")
    errors = []
    for key, typ in REQUIRED_KEYS.items():
        if key not in data:
            errors.append(f"missing required section: {key}")
        elif not isinstance(data[key], typ):
            errors.append(f"section {key!r} must be a {typ.__name__}")
    for i, ref in enumerate(data.get("referrals") or []):
        if not isinstance(ref, dict) or "company" not in ref or "status" not in ref:
            errors.append(f"referrals[{i}] needs at least 'company' and 'status'")
        elif ref["status"] not in ("have_referral", "requested", "none"):
            errors.append(f"referrals[{i}].status must be have_referral | requested | none")
    tiers = data.get("tiers") or {}
    if isinstance(tiers, dict):
        for name, weight in (tiers.get("companies") or {}).items():
            if not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
                errors.append(f"tiers.companies[{name!r}] must be a number in [0, 1]")
    if data.get("application_preferences", {}).get("auto_submit") is True:
        errors.append("application_preferences.auto_submit is true — this tool never submits; refusing to run")
    if errors:
        raise SystemExit("candidate.yaml failed validation:\n  - " + "\n  - ".join(errors))
    return data


def referral_companies(candidate: dict) -> set[str]:
    """Normalized company names with status=have_referral. Checked before anything
    enters the queue — applying cold to one of these is an irreversible mistake."""
    from intern_queue.dedupe import norm_company

    return {
        norm_company(r["company"])
        for r in candidate.get("referrals") or []
        if isinstance(r, dict) and r.get("status") == "have_referral" and r.get("company")
    }

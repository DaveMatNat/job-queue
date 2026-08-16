import json

import pytest

from intern_queue.enrich import SCHEMA, build_system, resume_for, validate


def test_resume_version_derived_from_role_class():
    assert resume_for("ml") == "ml"
    assert resume_for("research") == "ml"
    for rc in ("swe", "quant", "infra", "other"):
        assert resume_for(rc) == "swe"


def test_validate_requires_every_id_exactly_once():
    data = {"listings": [{"id": 1}, {"id": 2}]}
    assert len(validate(data, {1, 2})) == 2
    with pytest.raises(SystemExit, match="wrong ids"):
        validate({"listings": [{"id": 1}]}, {1, 2})  # dropped record
    with pytest.raises(SystemExit, match="wrong ids"):
        validate({"listings": [{"id": 1}, {"id": 1}]}, {1, 2})  # duplicate
    with pytest.raises(SystemExit, match="wrong ids"):
        validate({"listings": [{"id": 1}, {"id": 3}]}, {1, 2})  # invented id


def test_system_prompt_is_policy_verbatim_plus_scoped_excerpts(candidate):
    from pathlib import Path

    candidate["approved_facts"] = {"skills": {"languages": ["Python"]}}
    candidate["eligibility"] = {"work_authorization": ""}
    system = build_system(candidate)
    assert system.startswith(Path("POLICY.md").read_text())  # verbatim, at the top
    assert "approved_facts" in system and "eligibility" in system
    # nothing else about the candidate leaks into context
    for forbidden in ("identity", "phone", "gpa", "documents", "referrals", "tiers"):
        assert f"{forbidden}:" not in system.split("# candidate.yaml excerpts")[1]


def test_schema_is_strict():
    assert SCHEMA["additionalProperties"] is False
    item = SCHEMA["properties"]["listings"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"id", "knockouts", "role_class", "resume_version", "escalations", "note"}
    json.dumps(SCHEMA)  # serializable

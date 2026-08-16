import pytest

from intern_queue.candidate import load_candidate, referral_companies

VALID = """
identity: {preferred_name: D}
education: {university: X}
eligibility: {}
job_preferences: {}
application_preferences: {auto_submit: false}
documents: {}
approved_facts: {}
referrals:
  - {company: "Google LLC", status: have_referral}
  - {company: "Stripe", status: requested}
tiers:
  default: 0.5
  companies: {Google: 1.0}
"""


def test_valid_file_loads(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text(VALID)
    data = load_candidate(str(p))
    assert data["tiers"]["companies"]["Google"] == 1.0


def test_missing_file_fails_loudly(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        load_candidate(str(tmp_path / "nope.yaml"))


def test_invalid_yaml_fails_loudly(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text("identity: [unclosed")
    with pytest.raises(SystemExit, match="not valid YAML"):
        load_candidate(str(p))


def test_missing_sections_fail_loudly(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text("identity: {}\n")
    with pytest.raises(SystemExit, match="missing required section"):
        load_candidate(str(p))


def test_bad_referral_status_rejected(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text(VALID.replace("have_referral", "maybe"))
    with pytest.raises(SystemExit, match="status must be"):
        load_candidate(str(p))


def test_auto_submit_true_refuses_to_run(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text(VALID.replace("auto_submit: false", "auto_submit: true"))
    with pytest.raises(SystemExit, match="never submits"):
        load_candidate(str(p))


def test_referral_companies_normalized(tmp_path):
    p = tmp_path / "candidate.yaml"
    p.write_text(VALID)
    # "Google LLC" in the file matches listings that just say "Google";
    # "requested" referrals are not holds
    assert referral_companies(load_candidate(str(p))) == {"google"}

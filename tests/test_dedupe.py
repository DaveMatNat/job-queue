from intern_queue.dedupe import canonical_key, norm_company, norm_title, norm_text


def test_norm_text_strips_punctuation_and_case():
    assert norm_text("  Software   Engineer, Intern!  ") == "software engineer intern"


def test_norm_company_strips_corporate_suffixes():
    assert norm_company("Google LLC") == "google"
    assert norm_company("Crane Co.") == "crane"
    assert norm_company("Datadog, Inc.") == "datadog"
    # a suffix word alone is not stripped into nothing
    assert norm_company("Co") == "co"


def test_norm_title_strips_noise():
    assert norm_title("Software Engineer Intern - Summer 2027 - US") == "software engineer"
    assert norm_title("Software Engineer (Intern), 2027") == "software engineer"
    # but meaning-bearing words survive
    assert "machine learning" in norm_title("Machine Learning Intern — Summer 2027")


def test_same_job_three_sources_one_key():
    a = canonical_key("Google LLC", "Software Engineer Intern - Summer 2027", ["Mountain View, CA"])
    b = canonical_key("Google", "Software Engineer Intern", ["Mountain View, CA"])
    c = canonical_key("google", "Software Engineer - Intern - US", ["Mountain View, CA"])
    assert a == b == c


def test_different_roles_same_company_stay_separate():
    a = canonical_key("Google", "Software Engineer Intern", ["Mountain View, CA"])
    b = canonical_key("Google", "Machine Learning Engineer Intern", ["Mountain View, CA"])
    assert a != b


def test_different_first_location_stays_separate():
    a = canonical_key("Citadel", "Software Engineer Intern", ["Miami, FL"])
    b = canonical_key("Citadel", "Software Engineer Intern", ["New York, NY"])
    assert a != b

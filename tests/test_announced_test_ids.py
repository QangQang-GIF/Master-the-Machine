from runner.mcp_server import announced_test_ids

TEMPLATE = {"testResults": [{"id": "BUG-01-TC-01"}, {"id": "BUG-01-TC-02"}, {"id": "BUG-01-TC-03"}]}


def test_prose_cross_reference_is_not_a_requirement():
    details = {
        "submitTemplate": TEMPLATE,
        "minimumVerificationScenarios": "Expected: the same conditions as TC-01, measured on the production build.",
    }
    assert announced_test_ids(details) == ["BUG-01-TC-01", "BUG-01-TC-02", "BUG-01-TC-03"]


def test_mvs_text_fallback_keeps_only_qualified_ids():
    details = {"minimumVerificationScenarios": "BUG-02-TC-01 and BUG-02-TC-02; reuses the invoice from TC-01."}
    assert announced_test_ids(details) == ["BUG-02-TC-01", "BUG-02-TC-02"]


def test_bare_alias_of_qualified_id_is_dropped():
    details = {"submitTemplate": {"testResults": [{"id": "CR-01-TC-04"}, {"id": "TC-04"}]}}
    assert announced_test_ids(details) == ["CR-01-TC-04"]

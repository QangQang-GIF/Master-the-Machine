"""Regressions from practice match I5NLYST2."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mtm_competition.adapters.company_tools import CompanyToolsClient  # noqa: E402
from mtm_competition.scheduling.policy import announced_test_ids  # noqa: E402


def test_company_tools_endpoint_uses_game_master_origin_not_match_page():
    from runner.mcp_server import company_tools_origin
    base = company_tools_origin(None, None, None, "https://masterthemachine.tech/match")
    assert base == "https://masterthemachine.tech"
    client = CompanyToolsClient(base_url=base, match_id="I5NLYST2", api_key="k")
    assert client.endpoint == "https://masterthemachine.tech/api/v1/mcp/I5NLYST2"
    assert company_tools_origin("https://masterthemachine.tech", "x", None, "https://masterthemachine.tech/match") \
        == "https://masterthemachine.tech"
    assert company_tools_origin("https://gm.example.org/api/v1/", None) == "https://gm.example.org"


def test_template_rows_are_authoritative_over_prose_mentions():
    details = {
        "submitTemplate": {"testResults": [{"id": "BUG-09-TC-01"}, {"id": "BUG-09-TC-02"}, {"id": "BUG-09-TC-03"}]},
        "minimumVerificationScenarios": "## BUG-09-TC-02\nCreate schedules (schedules from TC-01 may stay).\n"
                                        "## BUG-09-TC-03\nThe capture of TC-01 is a PNG.",
    }
    assert announced_test_ids(details) == ["BUG-09-TC-01", "BUG-09-TC-02", "BUG-09-TC-03"]


def test_prose_only_drops_bare_ids_shadowed_by_qualified_ones():
    details = {"minimumVerificationScenarios": "CR-07-TC-01 ... see TC-01 ... CR-07-TC-02"}
    assert announced_test_ids(details) == ["CR-07-TC-01", "CR-07-TC-02"]

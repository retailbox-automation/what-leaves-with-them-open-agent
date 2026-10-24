"""Live: the real Nightshift on this Mac (scripts/nightshift_headless.sh start). Skipped unless NIGHTSHIFT_LIVE=1.

NIGHTSHIFT_LIVE_AGENTS picks which lanes are up (default investigator; add verifier when the Nano lane is loaded)."""

import os

import pytest

from open_agent import nightshift as N

pytestmark = pytest.mark.skipif(os.environ.get("NIGHTSHIFT_LIVE") != "1", reason="set NIGHTSHIFT_LIVE=1 with Nightshift up")
AGENTS = [a.strip() for a in os.environ.get("NIGHTSHIFT_LIVE_AGENTS", "investigator").split(",") if a.strip()]


@pytest.mark.parametrize("agent", AGENTS)
def test_each_agent_gets_its_nemotron_model_with_a_signed_zero_cost_receipt(agent):
    scope = N.new_scope("wlwt-livetest")
    calls = N.smoke(scope, (agent,))
    rc = calls[0]["receipt"]
    assert rc["routed_model"] == N.route_for(agent).model and rc["requested_model"] == N.route_for(agent).model
    assert rc["source_policy"] in ("local-only", f"pinned-model:{N.route_for(agent).model}")
    assert rc["cost_usd"] == 0 and rc["signed"] and rc["privacy"] == "local-only"
    if agent == "investigator":
        assert calls[0]["finish_reason"] == "tool_calls" and calls[0]["tool_calls"][0]["function"]["name"] == "current_owner"
    else:
        assert "verdict" in (calls[0]["content"] or "")

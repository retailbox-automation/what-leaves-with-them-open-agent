"""The agent loop: scripted model replies (no network), the router mode, and a live Ollama check if reachable."""

import json
import os
import urllib.request
from types import SimpleNamespace

import pytest

httpx = pytest.importorskip("httpx")
openai = pytest.importorskip("openai")

from honest_memory import answer as A
from honest_memory import agent as AG
from honest_memory.agent import Agent, AgentConfig


def tool_call(name, args, cid="c1"):
    return SimpleNamespace(id=cid, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def calls(*tcs):
    return SimpleNamespace(content="", tool_calls=list(tcs))


def says(text):
    return SimpleNamespace(content=text, tool_calls=None)


class FakeClient:
    """Replies with scripted messages in order and records every request."""

    def __init__(self, *messages, error=None):
        self.messages, self.requests, self.error = list(messages), [], error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # the loop keeps appending to its list
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=self.messages.pop(0))])


def agent(store, client=None, **kw):
    return Agent(store, name="agent:support", role="support", session="s1", client=client,
                 config=AgentConfig(model="fake" if client else None), **kw)


def test_router_mode_answers_with_no_model(fx_store):
    run = agent(fx_store).ask("Who owns billing-api?")
    assert (run.mode, run.rule, run.outcome, run.sentence_source) == ("router", "current_owner", A.ANSWERED_WITH_PATH, "template")
    assert run.sentence.startswith("Luca Ostrander (Platform) owns billing-api")
    assert run.trace[0].tool == "current_owner" and run.ms > 0
    assert run.to_dict()["answers"][0]["rows"][0]["owner"] == "Luca Ostrander"


def test_router_mode_says_when_no_tool_fits(fx_store):
    run = agent(fx_store).ask("What is the weather in Northfield?")
    assert run.outcome == "no_tool" and run.sentence == AG.NO_TOOL and run.answers == []


def test_model_picks_a_tool_and_its_sentence_is_kept_when_it_checks_out(fx_store):
    client = FakeClient(calls(tool_call("current_owner", {"system": "billing-api"})),
                        says("Luca Ostrander owns billing-api since 2026-09-22, per ADR-0009."))
    run = agent(fx_store, client).ask("Who owns billing-api?")
    assert (run.mode, run.sentence_source, run.check) == ("llm", "model", "ok")
    assert run.sentence == "Luca Ostrander owns billing-api since 2026-09-22, per ADR-0009."
    assert [a.tool for a in run.answers] == ["current_owner"]
    first, second = client.requests
    assert [t["function"]["name"] for t in first["tools"]][:2] == ["current_owner", "owner_at"] and first["temperature"] == 0
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and "Luca Ostrander" in tool_msg["content"] and "ADR-0009" in tool_msg["content"]
    assert "Never invent" in first["messages"][0]["content"] and "agent:support" in first["messages"][0]["content"]


def test_a_sentence_naming_something_not_in_the_rows_is_replaced_by_the_template(fx_store):
    client = FakeClient(calls(tool_call("current_owner", {"system": "billing-api"})),
                        says("Luca Ostrander owns billing-api, which sits behind auth-gateway."))
    run = agent(fx_store, client).ask("Who owns billing-api?")
    assert run.sentence_source == "template" and "auth-gateway" in run.check
    assert run.rejected.endswith("auth-gateway.") and "auth-gateway" not in run.sentence
    assert run.sentence.startswith("Luca Ostrander (Platform) owns billing-api")


def test_names_from_the_question_may_be_repeated(fx_store):
    client = FakeClient(calls(tool_call("current_owner", {"system": "billing-api"})),
                        says("No, Growth does not own billing-api; Luca Ostrander does."))
    run = agent(fx_store, client).ask("Is Growth the current owner of billing-api?")
    assert run.sentence_source == "model", run.check


def test_bad_arguments_go_back_to_the_model_which_can_retry(fx_store):
    client = FakeClient(calls(tool_call("impact", {"system": "ledger-db", "depth": 9})),
                        calls(tool_call("impact", {"system": "ledger-db", "depth": 2}, "c2")),
                        says("Two hops out, billing-api and reporting and invoice-renderer depend on ledger-db."))
    run = agent(fx_store, client).ask("What depends on ledger-db two hops out?")
    assert [a.tool for a in run.answers] == ["impact"] and run.answers[0].args["depth"] == 2
    assert run.errors == ["impact: depth must be an integer from 1 to 4"]
    assert "depth must be" in client.requests[1]["messages"][-1]["content"]
    assert run.sentence_source == "model"


def test_unknown_entity_is_reported_not_papered_over(fx_store):
    client = FakeClient(calls(tool_call("current_owner", {"system": "payment-service"})),
                        says("There is no system called payment-service in this memory, so I cannot say who owns it."))
    run = agent(fx_store, client).ask("Who owns payment-service?")
    assert run.outcome == A.UNKNOWN_ENTITY and run.sentence_source == "model"
    assert all("OWNS" not in q for q in run.trace[0].queries)


def test_the_model_may_not_claim_an_effect_the_rows_do_not_show(fx_store):
    client = FakeClient(calls(tool_call("impact", {"system": "webhooks"})),
                        says("If webhooks goes down, billing-api and auth will be affected."))
    run = agent(fx_store, client).ask("What breaks if webhooks goes down?")
    assert run.outcome == A.NOT_IN_GRAPH and run.sentence_source == "template"
    assert "not the same as nothing breaking" in run.sentence


def test_at_most_four_tool_calls(fx_store):
    many = [calls(tool_call("current_owner", {"system": "auth"}, f"c{i}")) for i in range(6)]
    client = FakeClient(*many[:4], says("Pavel Hollis owns auth."))
    run = agent(fx_store, client).ask("Who owns auth, over and over?")
    assert len(run.answers) == 4
    assert "tools" not in client.requests[-1]  # the last request asks for the sentence, with no tools offered


def test_a_model_that_calls_no_tool_gets_the_router_instead(fx_store):
    client = FakeClient(says("billing-api is owned by a team called Ghosts."))
    run = agent(fx_store, client).ask("Who owns billing-api?")
    assert "model called no tool" in run.rule and run.answers and "Ghosts" not in run.sentence
    assert run.sentence.startswith("Luca Ostrander")


def test_an_unreachable_model_falls_back_to_the_router(fx_store):
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://localhost:1/v1/chat/completions"))
    run = agent(fx_store, FakeClient(error=err)).ask("Who owns billing-api?")
    assert run.mode == "router" and run.outcome == A.ANSWERED_WITH_PATH
    assert run.errors and "model unavailable" in run.errors[0]


def test_session_and_agent_name_are_the_agents_own(fx_store):
    a = agent(fx_store)
    assert (a.ctx.agent, a.ctx.session) == ("agent:support", "s1")
    other = Agent(fx_store, name="agent:ops", role="ops")
    assert other.session.startswith("s-") and other.session != a.session


def test_config_reads_the_environment(monkeypatch):
    monkeypatch.delenv("HM_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("HM_LLM_MODEL", raising=False)
    monkeypatch.delenv("HM_LLM_API_KEY", raising=False)
    cfg = AgentConfig.from_env()
    assert (cfg.base_url, cfg.model, cfg.api_key) == ("http://localhost:11434/v1", None, "ollama")
    monkeypatch.setenv("HM_LLM_MODEL", "some-model")
    monkeypatch.setenv("HM_LLM_BASE_URL", "http://example.test/v1")
    cfg = AgentConfig.from_env()
    assert (cfg.model, cfg.base_url) == ("some-model", "http://example.test/v1")
    assert AgentConfig.from_env(default_model="x").model == "some-model"


# --- a real local model, only when it is there ---------------------------------------------

def ollama_has(model: str) -> bool:
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
            return model in {m["name"] for m in json.load(r)["models"]}
    except Exception:
        return False


@pytest.mark.skipif(not ollama_has(AG.DEFAULT_MODEL) or os.environ.get("HM_SKIP_LLM") == "1",
                    reason="local default model not reachable")
def test_default_local_model_calls_the_right_tool(fx_store):
    run = Agent(fx_store, config=AgentConfig(model=AG.DEFAULT_MODEL)).ask("Who currently owns billing-api?")
    assert run.mode == "llm" and run.answers[0].tool == "current_owner"
    assert run.answers[0].args == {"system": "billing-api"} and "Luca Ostrander" in run.sentence


def test_the_system_prompt_names_every_departure_tool_in_one_line_each():
    from honest_memory.agent import SYSTEM_PROMPT
    from honest_memory.departure import DEPARTURE_TOOLS
    lines = SYSTEM_PROMPT.splitlines()
    for tool in DEPARTURE_TOOLS:
        assert sum(1 for line in lines if line.startswith(f"- {tool.name}:")) == 1, tool.name

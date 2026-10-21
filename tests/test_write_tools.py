"""remember and handoff against the real write gate: argument mapping, identity, outcomes, what recall sees."""

import pytest

from honest_memory import answer as A
from honest_memory import tools as T
from honest_memory.agent import Agent

CTX = T.Context(agent="agent:support", session="s1", at="2026-10-15T12:00:00Z")


def test_remember_goes_through_the_gate_and_recall_sees_it(fx_store):
    ans = T.call(fx_store, "remember", {"text": "Customer asked about billing-api; Luca Ostrander owns it per ADR-0009.",
                                        "about": ["BILLING-API", "luca ostrander"], "source_ids": ["ADR-0009"]}, CTX)
    assert ans.outcome == A.ACCEPTED and ans.tool == "remember" and ans.sentence.startswith("The finding was accepted")
    assert ans.args["about"] == ["billing-api", "Luca Ostrander"]  # names were resolved before the gate saw them
    assert ans.receipts[0].outcome == "ACCEPT" and ans.receipts[0].cites == ["ADR-0009"]
    assert ans.rows[0]["record_id"].startswith("FND-") and ans.rows[0]["written"] is True
    assert [s.id for s in ans.sources] == ["ADR-0009"]
    assert any("MERGE" in q or "CREATE" in q for q in ans.trace[0].queries)  # the gate's writes are in the trace
    mem = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, CTX)
    new = next(r for r in mem.rows if r["kind"] == "finding" and r["session"] == "s1")
    assert (new["agent"], new["at"], new["source_ids"]) == ("agent:support", "2026-10-15T12:00:00Z", ["ADR-0009"])
    assert new["id"] == ans.rows[0]["record_id"] and new["text"].startswith("Customer asked")


def test_a_refusal_comes_back_as_refused_with_its_receipt(fx_store):
    ans = T.call(fx_store, "remember", {"text": "billing-api is owned by Growth.", "about": ["billing-api"]}, CTX)
    assert ans.outcome == A.REFUSED and ans.receipts[0].reasons == ["no_source"]
    assert "no_source" in ans.note and ans.sentence.startswith("Refused: no_source")
    after = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, CTX)
    assert not any("owned by Growth" in str(r.get("text")) for r in after.rows)


def test_personal_data_in_a_finding_is_refused_and_not_echoed(fx_store):
    ans = T.call(fx_store, "remember", {"text": "The customer on TCK-0044 can be reached at +1-555-0159.",
                                        "about": ["billing-api"], "source_ids": ["TCK-0044"]}, CTX)
    assert ans.outcome == A.REFUSED and ans.receipts[0].reasons == ["pii"]
    assert ans.args["text"] == "[redacted: personal data]" and ans.trace[0].args["text"] == ans.args["text"]
    assert "555" not in A.allowed_text(ans) and "555" not in str(ans.to_dict())


def test_a_name_that_is_not_in_the_graph_never_reaches_the_gate(fx_store):
    before = fx_store.ro("MATCH (g:GateDecision) RETURN count(g)")[0][0]
    ans = T.call(fx_store, "remember", {"text": "note", "about": ["billing-api", "payment-service"],
                                        "source_ids": ["ADR-0009"]}, CTX)
    assert ans.outcome == A.UNKNOWN_ENTITY and "payment-service" in ans.note
    assert fx_store.ro("MATCH (g:GateDecision) RETURN count(g)")[0][0] == before


def test_handoff_to_an_agent_that_has_not_written_yet(fx_store):
    ans = T.call(fx_store, "handoff", {"to": "agent:ops", "topic": "billing-api escalations from the customer",
                                       "note": "Customer waits for the owner.", "source_ids": ["TCK-0076"]}, CTX)
    assert ans.outcome == A.ACCEPTED and ans.args["to"] == "agent:ops" and ans.rows[0]["record_id"].startswith("HND-")
    mem = T.call(fx_store, "recall", {"entity": "billing-api", "limit": 20}, CTX)
    ho = next(r for r in mem.rows if r["kind"] == "handoff" and r["topic"].startswith("billing-api escalations from"))
    assert (ho["from"], ho["to"], ho["source_ids"]) == ("agent:support", "agent:ops", ["TCK-0076"])


def test_handoff_to_a_person_resolves_the_name_and_refuses_without_a_ticket(fx_store):
    ok = T.call(fx_store, "handoff", {"to": "selma marsh", "topic": "duplicate invoice emails", "source_ids": ["TCK-0077"]}, CTX)
    assert ok.outcome == A.ACCEPTED and ok.args["to"] == "Selma Marsh"
    bad = T.call(fx_store, "handoff", {"to": "Selma Marsh", "topic": "something", "source_ids": []}, CTX)
    assert bad.outcome == A.REFUSED and bad.receipts[0].reasons == ["no_source"]
    ghost = T.call(fx_store, "handoff", {"to": "Nobody Atall", "topic": "something", "source_ids": ["TCK-0077"]}, CTX)
    assert ghost.outcome == A.UNKNOWN_ENTITY


def test_the_gate_is_called_with_the_callers_identity_and_time(fx_store, monkeypatch):
    import honest_memory.gate as gate

    seen = {}

    def spy(store, agent, session, text, about, source_ids, at):
        seen.update(agent=agent, session=session, at=at, about=about)
        return gate.GateResult("CONFIRM", ["already_current"], "GD-x", False)

    monkeypatch.setattr(gate, "submit_finding", spy)
    ans = T.call(fx_store, "remember", {"text": "same thing again", "about": ["auth"], "source_ids": ["DOC-01"]}, CTX)
    assert seen == {"agent": "agent:support", "session": "s1", "at": "2026-10-15T12:00:00Z", "about": ["auth"]}
    assert ans.outcome == A.CONFIRMED


def test_a_model_cannot_choose_its_own_identity(fx_store):
    with pytest.raises(T.ArgError):
        T.call(fx_store, "remember", {"text": "note", "about": ["auth"], "source_ids": ["DOC-01"], "agent": "agent:ops"}, CTX)


def test_agent_object_stamps_name_and_session_on_writes(fx_store):
    agent = Agent(fx_store, name="agent:ops", role="ops", session="s2", ctx_at="2026-10-15T13:00:00Z")
    ans = agent.tool("remember", {"text": "ops checked the owner", "about": ["billing-api"], "source_ids": ["ADR-0009"]})
    assert ans.outcome == A.ACCEPTED
    row = fx_store.ro("MATCH (a:Agent)-[:WROTE]->(f:Finding {session: 's2'}) RETURN a.name, f.ts")
    assert row == [["agent:ops", 1792069200]]


def test_names_known_to_the_sentence_check_are_refreshed_after_a_write(fx_store):
    agent = Agent(fx_store, name="agent:newcomer", role="ops", session="s3")
    assert "agent:newcomer" not in agent.names()
    agent.tool("remember", {"text": "first note from a new agent", "about": ["auth"], "source_ids": ["DOC-01"]})
    assert "agent:newcomer" in agent.names()


def test_extractor_is_available_for_ingest():
    assert T.get_extractor() is not None

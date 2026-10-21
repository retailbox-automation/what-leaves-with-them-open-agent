"""Read tools against the fixture graph, on every FalkorDB in HM_TEST_PORTS."""

import pytest

from honest_memory import answer as A
from honest_memory import tools as T
from honest_memory.answer import check_sentence, template_sentence


def call(store, name, **args):
    return T.call(store, name, args)


# --- the contract every tool shares --------------------------------------------------------

CASES = [
    ("current_owner", {"system": "billing-api"}, A.ANSWERED_WITH_PATH),
    ("current_owner", {"system": "payment-service"}, A.UNKNOWN_ENTITY),
    ("current_owner", {"system": "webhooks"}, A.NOT_IN_GRAPH),
    ("owner_at", {"system": "billing-api", "date": "2026-09-09"}, A.ANSWERED_WITH_PATH),
    ("owner_at", {"system": "billing-api", "date": "2026-08-01"}, A.NOT_IN_GRAPH),
    ("history", {"system": "billing-api"}, A.ANSWERED_WITH_PATH),
    ("history", {"system": "webhooks"}, A.NOT_IN_GRAPH),
    ("escalation_path", {"system": "billing-api"}, A.ANSWERED_WITH_PATH),
    ("impact", {"system": "ledger-db"}, A.ANSWERED_WITH_PATH),
    ("impact", {"system": "webhooks"}, A.NOT_IN_GRAPH),
    ("impact", {"system": "payments-api"}, A.UNKNOWN_ENTITY),
    ("who_knows", {"system": "ledger-db"}, A.ANSWERED_WITH_PATH),
    ("who_knows", {"system": "webhooks"}, A.NOT_IN_GRAPH),
    ("decision_trail", {"decision": "ADR-0004"}, A.ANSWERED_WITH_PATH),
    ("decision_trail", {"decision": "ADR-0099"}, A.UNKNOWN_ENTITY),
    ("why", {"subject": "Growth", "predicate": "OWNS", "object": "billing-api"}, A.ANSWERED_WITH_PATH),
    ("why", {"subject": "Aiko Rademacher", "predicate": "DECIDED", "object": "ADR-0009"}, A.NOT_IN_GRAPH),
    ("what_changed", {"since": "2026-09-01", "predicate": "OWNS"}, A.ANSWERED_WITH_PATH),
    ("what_changed", {"since": "2026-09-23", "predicate": "OWNS"}, A.NOT_IN_GRAPH),
    ("path_between", {"a": "Sana Petrov", "b": "Priya Farrow"}, A.ANSWERED_WITH_PATH),
    ("path_between", {"a": "Sana Petrov", "b": "Nobody Atall"}, A.UNKNOWN_ENTITY),
    ("recall", {"entity": "billing-api"}, A.ANSWERED_FROM_MEMORY),
    ("recall", {"entity": "auth"}, A.NOT_IN_GRAPH),
    ("facts_about", {"entity": "Aiko Rademacher", "predicate": "MEMBER_OF"}, A.ANSWERED_WITH_PATH),
    # N5, the departure tools, on the same small graph (the seed-42 departure is in tests/test_departure.py)
    ("what_leaves", {"person": "Mateo Sandoval", "depth": 1}, A.ANSWERED_WITH_PATH),
    ("what_leaves", {"person": "Nobody Atall"}, A.UNKNOWN_ENTITY),
    ("single_source", {"person": "Selma Marsh"}, A.ANSWERED_WITH_PATH),
    ("single_source", {"person": "Pavel Hollis"}, A.NOT_IN_GRAPH),
    ("without", {"person": "Selma Marsh"}, A.ANSWERED_WITH_PATH),
    ("without", {"person": "Sana Petrov"}, A.NOT_IN_GRAPH),
    ("bridges", {"top": 3}, A.ANSWERED_WITH_PATH),
    ("successors", {"person": "Mateo Sandoval"}, A.ANSWERED_WITH_PATH),
    ("successors", {"person": "Sana Petrov"}, A.NOT_IN_GRAPH),
    ("handoff_plan", {"person": "Mateo Sandoval"}, A.ANSWERED_WITH_PATH),
    ("promises", {"person": "Priya Farrow"}, A.NOT_IN_GRAPH),
]


@pytest.mark.parametrize("name,args,outcome", CASES, ids=[f"{n}-{o}-{i}" for i, (n, _, o) in enumerate(CASES)])
def test_outcomes(fx_store, name, args, outcome):
    ans = call(fx_store, name, **args)
    assert ans.outcome == outcome
    assert ans.tool == name and ans.sentence
    assert len(ans.trace) == 1 and ans.trace[0].tool == name and ans.trace[0].ms >= 0
    names = T.all_names(fx_store)
    assert check_sentence(template_sentence(ans), ans, names) == (True, "ok"), template_sentence(ans)
    if outcome == A.ANSWERED_WITH_PATH:
        assert ans.rows
    if outcome in (A.UNKNOWN_ENTITY, A.NOT_IN_GRAPH):
        assert not ans.sentence.startswith("From memory")


def test_every_tool_is_covered_by_a_case(fx_store):
    read_tools = {n for n, t in T.TOOLS.items() if not t.writes}
    assert read_tools == {n for n, _, _ in CASES}


# --- current_owner / owner_at / history ----------------------------------------------------

def test_current_owner_has_path_to_its_sources(fx_store):
    ans = call(fx_store, "current_owner", system="billing-api")
    row = ans.rows[0]
    assert (row["owner"], row["team"], row["previous_owner"], row["previous_team"]) == (
        "Luca Ostrander", "Platform", "Sana Petrov", "Payments")
    assert row["since"] == "2026-09-22T15:28:00Z" and row["source_ids"] == ["ADR-0009", "msg-0148"]
    assert ["Luca Ostrander", "OWNS", "billing-api", "<-ABOUT", fx_store.gate_ids["luca_owns"], "CITES", "ADR-0009"] in ans.paths
    assert [s.id for s in ans.sources] == ["ADR-0009", "msg-0148"]
    assert ans.sources[0].title.startswith("ADR-0009") and ans.sources[0].ts == "2026-09-22T15:28:00Z"
    assert [r.id for r in ans.receipts] == [fx_store.gate_ids["luca_owns"]] and ans.receipts[0].outcome == "SUPERSEDE"


def test_names_resolve_case_insensitively(fx_store):
    ans = call(fx_store, "current_owner", system="  Billing-API ")
    assert ans.outcome == A.ANSWERED_WITH_PATH and ans.args == {"system": "billing-api"}


def test_owner_at_before_and_after_the_change(fx_store):
    before = call(fx_store, "owner_at", system="billing-api", date="2026-09-09")
    after = call(fx_store, "owner_at", system="billing-api", date="2026-09-30")
    assert [r["owner"] for r in before.rows] == ["Sana Petrov"] and before.paths == [["Sana Petrov", "WAS", "billing-api"]]
    assert [r["owner"] for r in after.rows] == ["Luca Ostrander"] and after.paths == [["Luca Ostrander", "OWNS", "billing-api"]]


def test_owner_at_on_the_day_of_the_change_returns_both(fx_store):
    ans = call(fx_store, "owner_at", system="billing-api", date="2026-09-22")
    assert [r["owner"] for r in ans.rows] == ["Sana Petrov", "Luca Ostrander"]
    instant = call(fx_store, "owner_at", system="billing-api", date="2026-09-22T10:00:00Z")
    assert [r["owner"] for r in instant.rows] == ["Sana Petrov"]


def test_history_is_chronological_with_state(fx_store):
    ans = call(fx_store, "history", system="billing-api")
    assert [(r["owner"], r["state"]) for r in ans.rows] == [("Sana Petrov", "closed"), ("Luca Ostrander", "current")]
    assert ans.rows[0]["to"] == ans.rows[1]["from"] and ans.rows[0]["closed_by"] == fx_store.gate_ids["luca_owns"]
    assert {s.id for s in ans.sources} == {"DOC-01", "RB-05", "ADR-0009", "msg-0148"}


# --- escalation_path / impact / who_knows ---------------------------------------------------

def test_escalation_path_walks_owner_team_lead(fx_store):
    ans = call(fx_store, "escalation_path", system="billing-api")
    assert ans.rows == [{"system": "billing-api", "owner": "Luca Ostrander", "team": "Platform", "lead": "Selma Marsh"}]
    assert ans.paths == [["billing-api", "<-OWNS", "Luca Ostrander", "MEMBER_OF", "Platform", "<-LEADS", "Selma Marsh"]]


def test_escalation_path_without_a_team_lead(fx_store):
    ans = call(fx_store, "escalation_path", system="reporting")
    assert ans.rows[0]["team"] == "Growth" and ans.rows[0]["lead"] is None
    assert "no team lead is recorded" in ans.sentence


def test_impact_depth_one_versus_four(fx_store):
    one = call(fx_store, "impact", system="ledger-db", depth=1)
    four = call(fx_store, "impact", system="ledger-db", depth=4)
    assert [r["system"] for r in one.rows] == ["billing-api", "reporting"]
    assert [(r["system"], r["hops"]) for r in four.rows] == [
        ("billing-api", 1), ("reporting", 1), ("invoice-renderer", 2), ("notifications", 3), ("status-page", 4)]
    far = next(p for p in four.paths if p[0] == "status-page")
    assert far == ["status-page", "DEPENDS_ON", "notifications", "DEPENDS_ON", "invoice-renderer",
                   "DEPENDS_ON", "billing-api", "DEPENDS_ON", "ledger-db"]
    assert four.rows[-1]["owner"] == "Priya Farrow" and four.rows[-1]["owner_team"] == "Support"


def test_impact_as_of_a_date_uses_the_dependencies_and_owners_of_that_date(fx_store):
    now = call(fx_store, "impact", system="billing-api")
    then = call(fx_store, "impact", system="billing-api", as_of="2026-09-10")
    assert [r["system"] for r in now.rows] == ["invoice-renderer", "reporting", "notifications", "status-page"]
    assert [r["system"] for r in then.rows] == ["invoice-renderer", "notifications", "status-page"]  # reporting came later
    assert then.args["as_of"] == "2026-09-10" and then.sentence.startswith("As of 2026-09-10, if billing-api")
    ledger_now = call(fx_store, "impact", system="ledger-db")
    ledger_then = call(fx_store, "impact", system="ledger-db", as_of="2026-09-10")
    owner = lambda ans, system: next(r["owner"] for r in ans.rows if r["system"] == system)
    assert (owner(ledger_now, "billing-api"), owner(ledger_then, "billing-api")) == ("Luca Ostrander", "Sana Petrov")
    assert [(r["system"], r["hops"]) for r in ledger_then.rows] == [
        ("billing-api", 1), ("reporting", 1), ("invoice-renderer", 2), ("notifications", 3), ("status-page", 4)]
    assert call(fx_store, "impact", system="ledger-db", as_of="2026-08-01").outcome == A.NOT_IN_GRAPH


def test_impact_rows_carry_the_documents_behind_each_dependent(fx_store):
    ans = call(fx_store, "impact", system="billing-api")
    rows = {r["system"]: r for r in ans.rows}
    assert rows["reporting"]["source_ids"][0] == "ADR-0009" and "DOC-01" in rows["invoice-renderer"]["source_ids"]
    assert {s.id for s in ans.sources} >= {"DOC-01", "ADR-0009"}
    empty = call(fx_store, "impact", system="webhooks")
    assert "come from ADR-0009, DOC-01" in empty.note and [s.id for s in empty.sources] == ["ADR-0009", "DOC-01"]


def test_impact_keeps_the_shortest_path_per_dependent(fx_store):
    ans = call(fx_store, "impact", system="ledger-db", depth=4)
    reporting = [p for p in ans.paths if p[0] == "reporting"]
    assert reporting == [["reporting", "DEPENDS_ON", "ledger-db"]]  # not also via billing-api


def test_impact_depth_is_bounded(fx_store):
    for bad in (0, 5, "deep", True):
        with pytest.raises(T.ArgError):
            call(fx_store, "impact", system="ledger-db", depth=bad)
    assert call(fx_store, "impact", system="ledger-db", depth="2").args["depth"] == 2  # a model may send "2"


def test_not_in_graph_is_not_unknown_entity(fx_store):
    isolated = call(fx_store, "impact", system="webhooks")
    missing = call(fx_store, "impact", system="webhook-gateway")
    assert (isolated.outcome, missing.outcome) == (A.NOT_IN_GRAPH, A.UNKNOWN_ENTITY)
    assert "not the same as nothing breaking" in isolated.note
    assert all("DEPENDS_ON" not in q for q in missing.trace[0].queries)  # the main query never ran
    assert any("DEPENDS_ON" in q for q in isolated.trace[0].queries)


def test_who_knows_ranks_by_count_and_breaks_ties_by_name(fx_store):
    ledger = call(fx_store, "who_knows", system="ledger-db")
    assert [(r["person"], r["count"], r["rank"]) for r in ledger.rows] == [
        ("Mateo Sandoval", 10, 1), ("Pavel Hollis", 7, 2), ("Sana Petrov", 5, 3),
        ("Luca Ostrander", 3, 4), ("Selma Marsh", 2, 5), ("Priya Farrow", 1, 6)]
    billing = call(fx_store, "who_knows", system="billing-api")
    assert [r["person"] for r in billing.rows] == ["Luca Ostrander", "Pavel Hollis", "Sana Petrov"]
    assert [r["person"] for r in call(fx_store, "who_knows", system="ledger-db", limit=2).rows] == [
        "Mateo Sandoval", "Pavel Hollis"]


# --- decision_trail / why / what_changed ----------------------------------------------------

def test_decision_trail_from_either_end(fx_store):
    for start in ("ADR-0004", "ADR-0011", "adr-0004"):
        ans = call(fx_store, "decision_trail", decision=start)
        assert [(r["decision"], r["status"]) for r in ans.rows] == [("ADR-0004", "superseded"), ("ADR-0011", "accepted")]
        assert ["ADR-0011", "SUPERSEDES", "ADR-0004"] in ans.paths
        assert {s.id for s in ans.sources} >= {"ADR-0011", "PM-03"}
    assert [r["decided_by"] for r in ans.rows] == ["Mateo Sandoval", "Pavel Hollis"]
    lone = call(fx_store, "decision_trail", decision="ADR-0009")
    assert [r["decision"] for r in lone.rows] == ["ADR-0009"] and "No supersession" in lone.note


def test_why_the_rumour_was_refused_and_what_is_held_instead(fx_store):
    ans = call(fx_store, "why", subject="growth", predicate="owns", object="billing-api")
    receipt = next(r for r in ans.rows if r["kind"] == "receipt")
    current = next(r for r in ans.rows if r["kind"] == "current")
    assert (receipt["outcome"], receipt["reasons"], receipt["cited"]) == ("REJECT", ["no_source", "conflicts_current"], [])
    assert (current["subject"], current["since"][:10]) == ("Luca Ostrander", "2026-09-22")
    assert "refused" in ans.sentence and "Luca Ostrander" in ans.sentence
    assert ans.receipts[0].id == "gd-0100" and receipt["claimed_in"] == "msg-0174"
    assert ["gd-0100", "CLAIMED_IN", "msg-0174"] in ans.paths and "msg-0174" in {s.id for s in ans.sources}


def test_why_finds_a_refused_claim_by_the_message_it_was_read_in(fx_store):
    ans = call(fx_store, "why", subject="msg-0174")
    assert ans.outcome == A.ANSWERED_WITH_PATH and [r["receipt"] for r in ans.rows] == ["gd-0100"]


def test_why_does_not_echo_refused_personal_data(fx_store):
    ans = call(fx_store, "why", subject="TCK-0044", predicate="HAS_PHONE")
    assert ans.outcome == A.ANSWERED_WITH_PATH
    assert ans.rows[0]["reasons"] == ["pii"] and ans.paths == [["gd-0102", "CITES", "TCK-0044"]]
    blob = A.allowed_text(ans)
    assert "555" not in blob and "[personal data]" in blob


def test_why_for_an_accepted_fact_shows_its_sources(fx_store):
    ans = call(fx_store, "why", subject="Luca Ostrander", predicate="OWNS", object="billing-api")
    assert ans.rows[0]["outcome"] == "SUPERSEDE" and ans.rows[0]["cited"] == ["ADR-0009", "msg-0148"]


def test_what_changed_lists_learned_closed_and_refused(fx_store):
    ans = call(fx_store, "what_changed", since="2026-09-01", predicate="OWNS")
    assert [(r["change"], r["subject"]) for r in ans.rows] == [
        ("learned", "Luca Ostrander"), ("closed", "Sana Petrov"), ("refused", "Growth"), ("refused", "Sana Petrov")]
    assert [r.id for r in ans.receipts] == ["gd-0100", "gd-0101", "gd-0900"]  # refusals, then the closing receipt
    closed = next(r for r in ans.rows if r["change"] == "closed")
    assert closed["closed_by"] == "SUPERSEDE" and "ADR-0009" in closed["source_ids"]


def test_what_changed_nothing_but_refused_claims_is_not_in_graph(fx_store):
    ans = call(fx_store, "what_changed", since="2026-09-23", predicate="OWNS")
    assert ans.outcome == A.NOT_IN_GRAPH and [r["change"] for r in ans.rows] == ["refused", "refused"]
    assert "Growth OWNS billing-api" in ans.sentence and "refused" in ans.sentence
    assert [r["claimed_in"] for r in ans.rows] == ["msg-0174", "msg-0181"]
    assert {s.id for s in ans.sources} == {"msg-0174", "msg-0181"}


def test_what_changed_since_is_strictly_after_the_day(fx_store):
    on_day = call(fx_store, "what_changed", since="2026-09-22", predicate="OWNS")
    assert [r["change"] for r in on_day.rows] == ["refused", "refused"]  # the 22nd itself is not "since"
    instant = call(fx_store, "what_changed", since="2026-09-22T15:00:00Z", predicate="OWNS")
    assert instant.rows[0]["change"] == "learned"


def test_what_changed_decided_lists_adrs(fx_store):
    ans = call(fx_store, "what_changed", since="2026-09-01", predicate="DECIDED")
    assert [r["object"] for r in ans.rows] == ["ADR-0011", "ADR-0009"]


# --- path_between / recall / facts_about -----------------------------------------------------

def test_path_between_marks_hops_walked_backwards(fx_store):
    ans = call(fx_store, "path_between", a="Sana Petrov", b="Priya Farrow")
    assert ans.paths == [["Sana Petrov", "WORKED_ON", "ledger-db", "<-WORKED_ON", "Priya Farrow"]]
    assert ans.rows == [{"from": "Sana Petrov", "to": "Priya Farrow", "hops": 2}]
    assert A.render_path(ans.paths[0]) == "Sana Petrov -WORKED_ON-> ledger-db <-WORKED_ON- Priya Farrow"


def test_path_between_respects_max_hops_and_rejects_same_entity(fx_store):
    short = call(fx_store, "path_between", a="Luca Ostrander", b="status-page", max_hops=1)
    assert short.outcome == A.NOT_IN_GRAPH and "not modelled" in short.note
    with pytest.raises(T.ArgError):
        call(fx_store, "path_between", a="auth", b="AUTH")


def test_path_between_crosses_labels(fx_store):
    ans = call(fx_store, "path_between", a="Selma Marsh", b="Platform")
    assert ans.paths == [["Selma Marsh", "LEADS", "Platform"]] or ans.paths[0][-1] == "Platform"


def test_recall_returns_dated_memory_newest_first_and_flags_stale_findings(fx_store):
    ans = call(fx_store, "recall", entity="billing-api")
    kinds = [(r["kind"], r["id"]) for r in ans.rows]
    assert kinds == [("handoff", "h-001"), ("finding", "f-002"), ("finding", "f-001")]
    findings = {r["id"]: r for r in ans.rows if r["kind"] == "finding"}
    assert findings["f-002"]["closed_since"] == 0 and findings["f-001"]["closed_since"] == 1  # f-001 predates the transfer
    assert findings["f-001"]["agent"] == "agent:support" and findings["f-001"]["at"] == "2026-09-10T10:00:00Z"
    assert ["agent:support", "WROTE", "f-002", "ABOUT", "billing-api", "DERIVED_FROM", "ADR-0009"] in ans.paths


def test_recall_finds_handoffs_by_endpoint_and_by_cited_episode(fx_store):
    by_team = call(fx_store, "recall", entity="Support")
    assert [(r["id"], r["from"], r["to"]) for r in by_team.rows] == [("h-002", "Support", "Selma Marsh")]
    via_mention = call(fx_store, "recall", entity="invoice-renderer")
    assert {r["id"] for r in via_mention.rows} == {"h-001", "h-002"}


def test_facts_about_directions_and_filter(fx_store):
    adr = call(fx_store, "facts_about", entity="ADR-0004")
    assert {(r["predicate"], r["direction"], r["other"]) for r in adr.rows} == {
        ("DECIDED", "in", "Mateo Sandoval"), ("SUPERSEDES", "in", "ADR-0011")}
    assert ["ADR-0004", "<-DECIDED", "Mateo Sandoval"] in adr.paths
    luca = call(fx_store, "facts_about", entity="Luca Ostrander", predicate="OWNS")
    assert [(r["predicate"], r["other"]) for r in luca.rows] == [("OWNS", "billing-api")]
    nothing = call(fx_store, "facts_about", entity="webhooks", predicate="OWNS")
    assert nothing.outcome == A.NOT_IN_GRAPH


# --- arguments, injection, catalogue ---------------------------------------------------------

def test_a_name_is_never_part_of_a_query(fx_store):
    hostile = "x'}) DETACH DELETE n //"
    ans = call(fx_store, "current_owner", system=hostile)
    assert ans.outcome == A.UNKNOWN_ENTITY and hostile in ans.note
    assert fx_store.ro("MATCH (n:System) RETURN count(n)")[0][0] == 8
    assert all(hostile not in q for q in ans.trace[0].queries)


def test_unknown_entity_suggests_a_close_name_but_assumes_nothing(fx_store):
    ans = call(fx_store, "current_owner", system="billing-apii")
    assert ans.outcome == A.UNKNOWN_ENTITY and "Closest names: billing-api" in ans.note and ans.rows == []


def test_malformed_calls_raise_arg_error(fx_store):
    for name, args in (("nope", {}), ("current_owner", {}), ("current_owner", {"system": 3}),
                       ("current_owner", {"system": "auth", "depth": 2}), ("owner_at", {"system": "auth", "date": "soon"}),
                       ("why", {"subject": "auth", "predicate": "LOVES"}), ("recall", {"entity": ""})):
        with pytest.raises(T.ArgError):
            T.call(fx_store, name, args)


def test_trace_rows_carry_tool_args_ms_rows_and_the_cypher(fx_store):
    ans = call(fx_store, "who_knows", system="ledger-db", limit=3)
    t = ans.trace[0]
    assert (t.tool, t.args, t.rows) == ("who_knows", {"system": "ledger-db", "limit": 3}, 3)
    assert t.ms > 0 and any("WORKED_ON" in q for q in t.queries)


def test_tool_schemas_describe_every_tool():
    schemas = T.tool_schemas()
    assert [s["name"] for s in schemas] == list(T.TOOLS) and len(schemas) == 21  # 14 + 7 departure tools (N5)
    depth = next(s for s in schemas if s["name"] == "impact")["parameters"]["properties"]["depth"]
    assert (depth["type"], depth["minimum"], depth["maximum"], depth["default"]) == ("integer", 1, 4, 4)
    for s in schemas:
        params = s["parameters"]
        assert params["type"] == "object" and params["additionalProperties"] is False
        assert set(params["required"]) <= set(params["properties"])
    oa = T.openai_tools(include_writes=False)
    assert {t["function"]["name"] for t in oa} == {n for n, t in T.TOOLS.items() if not t.writes}
    assert all(t["type"] == "function" for t in oa)


def test_schemas_validate_example_arguments():
    jsonschema = pytest.importorskip("jsonschema")
    schema = {s["name"]: s["parameters"] for s in T.tool_schemas()}
    jsonschema.validate({"system": "billing-api", "depth": 3}, schema["impact"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"system": "billing-api", "depth": 9}, schema["impact"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"system": "billing-api", "extra": 1}, schema["impact"])


def test_write_tools_need_the_gate(fx_store, monkeypatch):
    import sys

    import honest_memory

    monkeypatch.setitem(sys.modules, "honest_memory.gate", None)  # makes the import fail
    monkeypatch.delattr(honest_memory, "gate", raising=False)
    with pytest.raises(T.GateUnavailable):
        call(fx_store, "remember", text="note about billing-api", about=["billing-api"], source_ids=["ADR-0009"])
    unknown = call(fx_store, "remember", text="note", about=["billing-apix"], source_ids=[])
    assert unknown.outcome == A.UNKNOWN_ENTITY  # names are resolved before the gate is touched

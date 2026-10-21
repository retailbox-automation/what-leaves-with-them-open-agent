"""N5, the departure tools, on the seed-42 corpus ingested through the extractor and the gate, on every engine.

The leaver of seed 42 is Oskar Alvarez (Platform): resignation DOC-15 on 2026-09-25, handoff session DOC-17 (his
answers, 2026-10-01) and DOC-18 (Viktor Hartwell confirms, 2026-10-02), last day DOC-16 on 2026-10-09.
Expected values are read off the generator's own plan where it has one (tests/test_corpus.py checks the plan).
"""

import pytest

from honest_memory import answer as A
from honest_memory import departure as D
from honest_memory import tools as T
from honest_memory.answer import check_sentence, template_sentence
from honest_memory.corpus.generator import betweenness as exact_betweenness, build, write_corpus
from honest_memory.ingest import ingest_corpus
from honest_memory.schema import graph_name
from tests.conftest import PORTS, make_store

L = "Oskar Alvarez"
CORPUS = build(42)


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def corpus_store(request, tmp_path_factory):
    out = write_corpus(CORPUS, tmp_path_factory.mktemp("corpus") / "corpus")
    s = make_store(request.param, f"n5{request.param}")
    ingest_corpus(s, out)
    s.corpus_dir = out
    yield s
    s.drop()


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def corpus_before_session(request, tmp_path_factory):
    """The memory during the notice period, before the handoff session (DOC-17, 2026-10-01)."""
    out = write_corpus(CORPUS, tmp_path_factory.mktemp("corpus") / "corpus")
    s = make_store(request.param, f"n5pre{request.param}")
    ingest_corpus(s, out, until="2026-10-01")
    yield s
    s.drop()


def call(store, name, **args):
    ans = T.call(store, name, args, T.Context(agent="agent:test", session="s-n5", at="2026-10-02T09:00:00Z"))
    ok, why = check_sentence(ans.sentence, ans, T.all_names(store))
    assert ok, (why, ans.sentence)
    assert ans.sentence == template_sentence(ans)
    return ans


def kind(ans, k):
    return [r for r in ans.rows if r.get("kind") == k]


def leftovers(store):
    prefix = graph_name(store.tenant) + "__"
    return [g for g in store.db.list_graphs() if g.startswith(prefix)]


# ---- what_leaves: depth 1 against depth 4 -------------------------------------------------------------------------
def test_depth_1_is_what_a_join_would_list(corpus_store):
    ans = call(corpus_store, "what_leaves", person=L, depth=1)
    held = kind(ans, "held")
    assert [(r["relation"], r["item"], r["state"]) for r in held] == [
        ("OWNS", "fernhill-sso", "ended"), ("OWNS", "oncall-runbook", "ended"), ("MEMBER_OF", "Platform", "ended")]
    assert all(r["until"] == "2026-10-09T22:00:00Z" and r["closed_by"].startswith("GD-") for r in held)
    assert ans.paths == [[L, "WAS", "fernhill-sso"], [L, "WAS", "oncall-runbook"], [L, "WAS", "Platform"]]
    assert [r.outcome for r in ans.receipts] == ["CLOSE"] * 3 and all(r.cites == ["DOC-16"] for r in ans.receipts)
    assert not kind(ans, "at_risk") and ans.rows[0]["depth_1"] == ans.rows[0]["depth_n"]


def test_depth_4_adds_decisions_that_govern_other_teams_systems(corpus_store):
    ans = call(corpus_store, "what_leaves", person=L, depth=4)
    risk = {r["item"]: r for r in kind(ans, "at_risk")}
    assert {i: (r["hops"], r["decision"], r["owner"], r["owner_team"]) for i, r in risk.items()} == {
        "auth": (2, "ADR-0016", "Pavel Hollis", "Platform"),
        "ledger-db": (2, "ADR-0014", "Mateo Sandoval", "Payments"),
        "reporting": (2, "ADR-0015", "Aiko Rademacher", "Growth"),
        "billing-api": (3, "ADR-0014", "Luca Ostrander", "Platform"),
        "admin-console": (4, "ADR-0014", "Priya Farrow", "Support"),
        "invoice-renderer": (4, "ADR-0014", "Viktor Hartwell", "Payments"),
        "webhooks": (4, "ADR-0014", "Luca Ostrander", "Platform")}
    assert risk["auth"]["co_deciders"] == ["Pavel Hollis"] and risk["ledger-db"]["co_deciders"] == []
    assert risk["ledger-db"]["other_team"] and not risk["auth"]["other_team"]
    path = next(p for p in ans.paths if "admin-console" in p)
    assert path == [L, "DECIDED", "ADR-0014", "CONSTRAINS", "ledger-db", "<-DEPENDS_ON", "billing-api",
                    "<-DEPENDS_ON", "admin-console", "<-OWNS", "Priya Farrow", "MEMBER_OF", "Support"]
    summary = ans.rows[0]
    assert summary["depth_1"] == ["fernhill-sso", "oncall-runbook", "Platform"] and len(summary["depth_n"]) == 10
    assert summary["by_hops"] == {"1": 3, "2": 3, "3": 1, "4": 3}
    assert summary["teams_reached"] == ["Growth", "Payments", "Platform", "Support"]
    assert {"ADR-0014", "ADR-0015", "ADR-0016", "ARCH-01"} <= {s.id for s in ans.sources}


@pytest.mark.parametrize("depth,count", [(1, 0), (2, 3), (3, 4), (4, 7)])
def test_depth_bounds_the_walk(corpus_store, depth, count):
    risk = kind(call(corpus_store, "what_leaves", person=L, depth=depth), "at_risk")
    assert len(risk) == count and all(r["hops"] <= depth for r in risk)


def test_as_of_a_date_before_the_last_day_shows_what_was_still_held(corpus_store):
    ans = call(corpus_store, "what_leaves", person=L, depth=1, as_of="2026-10-01")
    assert [(r["item"], r["state"]) for r in kind(ans, "held")] == [
        ("fernhill-sso", "current"), ("oncall-runbook", "current"), ("Platform", "current")]
    before = call(corpus_store, "what_leaves", person=L, depth=4, as_of="2026-08-30")
    assert [r["item"] for r in kind(before, "held")] == ["fernhill-sso", "Platform"]  # oncall-runbook came on 08-31
    assert kind(before, "at_risk") == []  # no decision of theirs was accepted yet


# ---- single source -----------------------------------------------------------------------------------------------------
def test_single_source_before_the_handoff_session_lists_lone_decisions_and_systems_not_the_shared_one(corpus_store):
    ans = call(corpus_store, "single_source", person=L, as_of="2026-09-30")
    by = {r["item"]: r for r in ans.rows[1:]}
    single = sorted(i for i, r in by.items() if r["single_source"])
    assert single == ["ADR-0014", "ADR-0015", "fernhill-sso", "oncall-runbook"]
    assert by["ADR-0016"]["single_source"] is False and by["ADR-0016"]["authors"] == ["Pavel Hollis"]  # control
    assert by["ADR-0014"]["episodes"] == ["ADR-0014"] and "DOC-14" in by["fernhill-sso"]["episodes"]
    assert not by["reporting"]["single_source"] and len(by["reporting"]["authors"]) > 1
    assert ans.rows[0]["single_source"] == 4 and [L, "DECIDED", "ADR-0014"] in ans.paths


def test_the_handoff_session_gives_the_lone_decisions_a_second_author(corpus_store):
    """Item 8: the counts before and after the session, read with as_of, the same way on every engine."""
    counts = {}
    for day in ("2026-09-30", "2026-10-05"):
        s = call(corpus_store, "single_source", person=L, as_of=day).rows[0]
        w = call(corpus_store, "what_leaves", person=L, depth=4, as_of=day).rows[0]
        counts[day] = (s["decisions"], s["systems"], w["decisions_only_theirs"])
    assert counts == {"2026-09-30": (2, 2, ["ADR-0014", "ADR-0015"]), "2026-10-05": (0, 2, [])}
    now = {r["item"]: r for r in call(corpus_store, "single_source", person=L).rows[1:]}
    assert now["ADR-0014"]["authors"] == [L, "Viktor Hartwell"] and "DOC-18" in now["ADR-0014"]["episodes"]
    assert now["fernhill-sso"]["single_source"] and "DOC-17" in now["fernhill-sso"]["episodes"]  # his answer alone
    receipts = corpus_store.ro("MATCH (g:GateDecision) WHERE g.claim_episode IN ['DOC-17', 'DOC-18'] "
                               "RETURN g.claim_episode, g.outcome, g.subject, g.predicate, g.object ORDER BY g.id")
    assert receipts == [["DOC-17", "CONFIRM", "ADR-0014", "CONSTRAINS", "ledger-db"],
                        ["DOC-17", "CONFIRM", "ADR-0015", "CONSTRAINS", "reporting"],
                        ["DOC-17", "CONFIRM", "fernhill-sso", "DEPENDS_ON", "auth"],
                        ["DOC-18", "CONFIRM", "ADR-0014", "CONSTRAINS", "ledger-db"],
                        ["DOC-18", "CONFIRM", "ADR-0015", "CONSTRAINS", "reporting"]]
    risk = {r["item"]: r for r in kind(call(corpus_store, "what_leaves", person=L, depth=2, as_of="2026-09-30"),
                                       "at_risk")}
    assert risk["ledger-db"]["only_theirs"] is True and risk["auth"]["only_theirs"] is False


def test_single_source_as_of_reads_only_what_existed_then(corpus_store):
    early = call(corpus_store, "single_source", person=L, as_of="2026-09-05")
    assert sorted(r["item"] for r in early.rows[1:] if r["single_source"]) == ["ADR-0014", "fernhill-sso",
                                                                              "oncall-runbook"]


# ---- without: islands, wall time, no temporary graph left behind --------------------------------------------------------
def test_without_finds_the_two_islands_and_measures_its_time(corpus_store):
    ans = call(corpus_store, "without", person=L)
    summary, islands = ans.rows[0], kind(ans, "island")
    assert [i["items"] for i in islands] == [["fernhill-sso"], ["oncall-runbook"]]
    assert summary["already_unconnected"] == 0 and summary["islands"] == 2
    assert 0 < summary["wall_ms"] < 2000
    through = {(c["relation"], c["item"], c["state"]) for i in islands for c in i["connected_through"]}
    assert through == {("WORKED_ON", "fernhill-sso", "current"), ("OWNS", "fernhill-sso", "ended"),
                       ("WORKED_ON", "oncall-runbook", "current"), ("OWNS", "oncall-runbook", "ended")}
    assert any("algo.WCC" in q for q in ans.trace[0].queries)
    assert leftovers(corpus_store) == []


def test_without_someone_who_holds_nothing_alone_cuts_nothing_off(corpus_store):
    ans = call(corpus_store, "without", person="Lars Thorne")  # works on four systems, owns and decided nothing
    assert ans.outcome == A.NOT_IN_GRAPH and "still connected" in ans.note
    lone_decider = call(corpus_store, "without", person="Viktor Hartwell")  # 0.2.0 ADRs record no CONSTRAINS
    assert [i["items"] for i in kind(lone_decider, "island")] == [["ADR-0011"], ["ADR-0012"]]


def test_without_drops_its_temporary_graph_even_when_a_query_fails(corpus_store, monkeypatch):
    monkeypatch.setattr(D, "_WCC", "CALL algo.noSuchAlgorithm() YIELD node RETURN node")
    with pytest.raises(Exception):
        T.call(corpus_store, "without", {"person": L})
    assert leftovers(corpus_store) == []


# ---- bridges -------------------------------------------------------------------------------------------------------------
def test_the_leaver_is_the_top_bridge_and_the_only_link_between_support_and_growth(corpus_store):
    ans = call(corpus_store, "bridges", top=3)
    top = kind(ans, "bridge")
    assert top[0]["person"] == L and top[0]["teams"] == ["Growth", "Payments", "Platform", "Support"]
    assert top[0]["only_link"] == [["Growth", "Support"]]
    assert {"teams": ["Growth", "Support"], "person": L, "kind": "only_link"} in ans.rows
    assert leftovers(corpus_store) == []


def test_bridge_scores_match_an_exact_computation_when_the_engine_is_exact(corpus_store):
    ans = call(corpus_store, "bridges", top=5)
    plan_facts = [g for g in CORPUS.gold if not g["invalid_at"] and g["predicate"] in ("MEMBER_OF", "LEADS", "OWNS",
                                                                                       "WORKED_ON")
                  and g.get("expected_gate", {}).get("outcome", "ACCEPT") == "ACCEPT"]
    people = {g["subject"] for g in CORPUS.gold if g["predicate"] == "MEMBER_OF"}
    nodes = people | {"Payments", "Platform", "Support", "Growth"} | {g["object"] for g in plan_facts
                                                                       if g["predicate"] in ("OWNS", "WORKED_ON")}
    nodes |= {"fernhill-sso", "oncall-runbook"}
    pairs = {frozenset((g["subject"], g["object"])) for g in plan_facts if g["object"] in nodes}
    want = exact_betweenness(nodes, pairs)
    ranked = sorted(people, key=lambda p: (-round(want[p], 6), p))[:5]
    got = kind(ans, "bridge")
    if ans.rows[0]["exact"]:  # 6.0.x: directed scores over a graph written both ways = twice the undirected score
        assert [r["person"] for r in got] == ranked
        assert [r["score"] for r in got] == [round(2 * want[p], 2) for p in ranked]
    else:  # 4.22 samples sources: the top bridge is clear enough to survive the estimate
        assert got[0]["person"] == ranked[0] and ans.rows[0]["runs"] == D.ESTIMATE_RUNS and "estimates" in ans.note


# ---- successors --------------------------------------------------------------------------------------------------------------
def test_successors_are_candidates_with_paths_and_honest_gaps(corpus_before_session):
    ans = call(corpus_before_session, "successors", person=L)
    rows = {r["item"]: r for r in ans.rows}
    assert list(rows) == ["fernhill-sso", "oncall-runbook", "ADR-0014", "ADR-0015"]
    assert [c["person"] for c in rows["fernhill-sso"]["candidates"]] == ["Lars Thorne", "Pavel Hollis", "Sana Petrov"]
    assert rows["fernhill-sso"]["candidates"][0]["via"] == ["auth"]
    assert rows["oncall-runbook"]["candidates"] == [] and rows["oncall-runbook"]["note"] == \
        "no one in the graph has worked on this"
    for decision, system in (("ADR-0014", "ledger-db"), ("ADR-0015", "reporting")):  # as who_knows ranks them then
        ranked = [r["person"] for r in T.call(corpus_before_session, "who_knows", {"system": system}).rows
                  if r["person"] != L][:3]
        assert [c["person"] for c in rows[decision]["candidates"]] == ranked and ranked[0] == "Viktor Hartwell"
    assert all(c["status"] == "candidate" for r in rows.values() for c in r["candidates"])
    assert ["Lars Thorne", "WORKED_ON", "auth", "<-DEPENDS_ON", "fernhill-sso"] in ans.paths
    assert ["Viktor Hartwell", "WORKED_ON", "ledger-db", "<-CONSTRAINS", "ADR-0014"] in ans.paths
    assert "decided" not in ans.sentence.lower().replace("not decisions", "")


def test_after_the_session_the_confirmed_decisions_are_no_longer_orphaned(corpus_store):
    rows = call(corpus_store, "successors", person=L).rows
    assert [r["item"] for r in rows] == ["fernhill-sso", "oncall-runbook"]


# ---- handoff_plan: read-only, then proposed through the gate ----------------------------------------------------------------
def handoffs(store):
    return store.ro("MATCH (h:Handoff) RETURN h.status, h.topic ORDER BY h.topic")


def test_handoff_plan_is_read_only_by_default(corpus_before_session):
    corpus_store = corpus_before_session
    before = handoffs(corpus_store)
    ans = call(corpus_store, "handoff_plan", person=L)
    assert handoffs(corpus_store) == before
    lines = kind(ans, "line")
    assert [(r["item"], r["candidates"][:1], r["review_with"]) for r in lines] == [
        ("fernhill-sso", ["Lars Thorne"], []), ("oncall-runbook", [], []),
        ("ADR-0014", ["Viktor Hartwell"], ["Mateo Sandoval"]), ("ADR-0015", ["Viktor Hartwell"], ["Aiko Rademacher"])]
    assert "nobody else is connected to it" in lines[1]["risk"] and "single source" in lines[2]["risk"]
    assert all(r["status"] == "plan" and "2026-10-09" in r["action"] for r in lines)
    assert ans.rows[0]["last_day"] == "2026-10-09" and ans.rows[0]["notice_source"] == "DOC-15"
    assert ans.paths[2][:5] == [L, "DECIDED", "ADR-0014", "CONSTRAINS", "ledger-db"]
    assert leftovers(corpus_store) == []
    questions = kind(ans, "question")
    assert [q["item"] for q in questions] == ["fernhill-sso", "oncall-runbook", "ADR-0014", "ADR-0015", "promises"]
    assert all(q["status"] == "open" and q["answered_in"] == [] for q in questions)
    assert questions[2]["question"] == ("Why was ADR-0014 decided, and what breaks in ledger-db (owner Mateo Sandoval) "
                                        "if it is undone?")
    assert questions[2]["sources_so_far"] == ["ADR-0014"]
    assert "Nobody else in the graph has worked on it" in questions[1]["question"]
    assert "including for fernhill-sso?" in questions[4]["question"] and "calls and meetings" in questions[4]["why"]


def test_after_the_session_the_plan_asks_only_what_is_still_open(corpus_store):
    ans = call(corpus_store, "handoff_plan", person=L)
    assert [r["item"] for r in kind(ans, "line")] == ["fernhill-sso", "oncall-runbook"]
    questions = {q["item"]: q for q in kind(ans, "question")}
    assert list(questions) == ["fernhill-sso", "oncall-runbook", "promises"]
    assert questions["fernhill-sso"]["answered_in"] == ["DOC-17"]
    assert questions["fernhill-sso"]["status"] == "answered by the leaver, waiting for a second person"
    assert questions["oncall-runbook"]["status"] == questions["promises"]["status"] == "open"  # unanswered on purpose
    assert ans.rows[0]["questions"] == 3


def test_handoff_plan_with_propose_writes_proposed_handoffs_through_the_gate(port, tmp_path):
    s = make_store(port, f"n5propose{port}")
    try:
        ingest_corpus(s, write_corpus(CORPUS, tmp_path / "corpus"))
        ans = call(s, "handoff_plan", person=L, propose=True)
        lines = kind(ans, "line")
        assert [(r["to"], r["gate"]) for r in lines] == [("Lars Thorne", "ACCEPT"), ("Platform", "ACCEPT")]
        assert [r.outcome for r in ans.receipts] == ["ACCEPT"] * 2 and ans.rows[0]["proposed"] == 2
        stored = s.ro("MATCH (h:Handoff {status: 'proposed'})-[:FROM]->(p:Person {name: $l}) "
                      "MATCH (h)-[:TO]->(t) RETURN h.topic, coalesce(t.name, t.id) ORDER BY h.topic", {"l": L})
        assert stored == [["Handoff: fernhill-sso", "Lars Thorne"], ["Handoff: oncall-runbook", "Platform"]]
    finally:
        s.drop()


# ---- promises: the honest not-in-graph -----------------------------------------------------------------------------------------
def test_no_phone_promise_is_an_honest_not_in_graph_with_the_channels_searched(corpus_store):
    ans = call(corpus_store, "promises", person=L, to="Fernhill Outfitters")
    assert ans.outcome == A.NOT_IN_GRAPH
    searched = ans.rows[0]
    assert searched["since"] == "2026-09-25" and searched["records"] == 11  # + DOC-17, DOC-18 of the session
    assert searched["by_channel"] == {"chat": 4, "ticket": 4, "doc": 3}
    assert "phone calls" in ans.note.lower() and "DOC-15" in ans.note and [s.id for s in ans.sources] == ["DOC-15"]


def test_a_written_promise_is_found_and_personal_data_stays_out(fx_store):
    fx_store.rw("CREATE (e:Episode {id: 'msg-0300', ts: 1790000000, channel: 'chat', author: 'Priya Farrow', "
                "title: '#support', text: 'I promised the customer a callback on +1-555-0142 by Friday.'})")
    fx_store.rw("CREATE (e:Episode {id: 'msg-0301', ts: 1790000100, channel: 'chat', author: 'Priya Farrow', "
                "title: '#support', text: 'I promised Fernhill Outfitters the export by Monday.'})")
    ans = call(fx_store, "promises", person="Priya Farrow", since="2026-09-01")
    found = kind(ans, "promise")
    assert [r["episode"] for r in found] == ["msg-0300", "msg-0301"]
    assert found[0]["line"] == "[redacted: personal data]" and "555" not in str(ans.to_dict())
    only = call(fx_store, "promises", person="Priya Farrow", since="2026-09-01", to="Fernhill Outfitters")
    assert [r["episode"] for r in kind(only, "promise")] == ["msg-0301"]


# ---- the orphan, answered honestly ----------------------------------------------------------------------------------------------
def test_current_owner_of_an_orphaned_system_names_the_last_owner_and_the_closing_receipt(corpus_store):
    ans = call(corpus_store, "current_owner", system="fernhill-sso")
    row = ans.rows[0]
    assert ans.outcome == A.NOT_IN_GRAPH and row["owner"] is None
    assert (row["last_owner"], row["last_until"], row["closed_outcome"], row["ended_by"]) == (
        L, "2026-10-09T22:00:00Z", "CLOSE", ["DOC-16"])
    assert ans.note.startswith("fernhill-sso has no current owner. Last owner: Oskar Alvarez until 2026-10-09")
    assert [r.outcome for r in ans.receipts] == ["CLOSE"]


def test_handoff_action_names_every_candidate():
    from honest_memory.departure import _action
    many = {"item": "fernhill-sso", "item_kind": "system",
            "candidates": [{"person": "Lars Thorne"}, {"person": "Pavel Hollis"}, {"person": "Sana Petrov"}]}
    text = _action("Oskar Alvarez", many, "before 2026-10-09")
    assert all(n in text for n in ("Lars Thorne", "Pavel Hollis", "Sana Petrov")) and "(candidates)" in text
    one = {**many, "candidates": [{"person": "Lars Thorne"}]}
    assert _action("Oskar Alvarez", one, "before 2026-10-09").startswith("Ask Lars Thorne (candidate) to take over")

"""Write gate: one test per check and per outcome, on every FalkorDB in HM_TEST_PORTS."""

import json
import os

import pytest

from honest_memory.gate import Candidate, GateResult, detect_pii, submit_fact, submit_finding, submit_handoff
from honest_memory.store import Store, epoch

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]

ENTITIES = [("Person", "Ana"), ("Person", "Bo"), ("Person", "Cy"), ("Team", "Payments"), ("Team", "Platform"),
            ("System", "billing-api"), ("System", "auth"), ("System", "ledger-db"), ("Decision", "ADR-0004"),
            ("Decision", "ADR-0011"), ("Project", "Ledger Hardening")]

EPISODES = [  # id, ts, channel, author, text
    ("DOC-01", "2026-08-17T12:00:00Z", "doc", "Cy",
     "Service catalog. billing-api is owned by Ana (Payments). auth is owned by Cy (Platform)."),
    ("DOC-03", "2026-08-18T10:00:00Z", "doc", "Cy", "Ana joined the Payments team."),
    ("DOC-04", "2026-08-19T10:00:00Z", "doc", "Cy", "Author: Ana. Reviewed by Cy."),
    ("DOC-05", "2026-08-20T10:00:00Z", "doc", "Cy", "Zed owns auth."),
    ("DOC-06", "2026-08-17T13:00:00Z", "doc", "Cy",
     "Roster. Ana: Payments Lead, Payments team. Bo: Backend Engineer, Platform team."),
    ("DOC-07", "2026-09-01T10:00:00Z", "doc", "Cy", "Ana moved to the Platform team."),
    ("DOC-08", "2026-08-18T14:00:00Z", "doc", "Cy", "Project charter: Ledger Hardening. Lead: Ana (Payments)."),
    ("PM-03", "2026-09-10T14:00:00Z", "doc", "Cy", "Decision: ADR-0011 supersedes ADR-0004."),
    ("ADR-0009", "2026-09-22T15:28:00Z", "doc", "Cy", "ADR-0009: ownership of billing-api moves from Ana to Bo."),
    ("TCK-0001", "2026-09-23T10:00:00Z", "ticket", "Ana", "Fixed proration in billing-api."),
    ("TCK-0004", "2026-09-24T10:00:00Z", "ticket", "Ana", "Tuned the billing-api cache."),
    ("TCK-0005", "2026-09-25T10:00:00Z", "ticket", "Ana", "Reviewed billing-api retries."),
    ("msg-0100", "2026-10-01T14:00:00Z", "chat", "Bo", "Quoting the runbook: Ana owns billing-api."),
    ("DOC-09", "2026-08-21T10:00:00Z", "doc", "Cy", "Architecture. billing-api depends on ledger-db."),
]


def seed(store: Store) -> Store:
    store.drop()
    store.ensure_schema()
    for label, name in ENTITIES:
        store.merge_entity(label, name)
    for eid, ts, channel, author, text in EPISODES:
        store.merge_entity("Episode", eid, {"ts": epoch(ts), "channel": channel, "author": author, "title": "", "text": text})
    return store


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def store(request):
    from falkordb import FalkorDB

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=request.param, socket_timeout=10)
    s = seed(Store(db, f"test_gate_{os.getpid()}"))
    yield s
    s.drop()


def cand(**kw) -> Candidate:
    base = dict(subject="Ana", predicate="OWNS", object="billing-api", valid_at="2026-08-17T12:00:00Z",
                source_ids=["DOC-01"])
    return Candidate(**{**base, **kw})


def count(store, query):
    return store.ro(query)[0][0]


def typed_edges(store, predicate):
    return count(store, f"MATCH ()-[r:{predicate}]->() RETURN count(r)")


def receipt(store, result: GateResult) -> dict:
    return store.records("GateDecision", [result.receipt_id])[result.receipt_id]


# ---- Candidate and GateResult shape ------------------------------------------------------------
def test_candidate_defaults_are_not_shared():
    a, b = cand(), cand()
    a.attrs["x"] = 1
    a.source_ids.append("TCK-0001")
    assert b.attrs == {} and b.source_ids == ["DOC-01"] and a.proposer == "rules" and a.claim_episode is None
    assert GateResult("ACCEPT", [], "GD-1", True).closed == []


# ---- ACCEPT and the receipt ------------------------------------------------------------------
def test_accept_writes_typed_edge_and_receipt(store):
    res = submit_fact(store, cand())
    assert (res.outcome, res.reasons, res.written, res.closed) == ("ACCEPT", ["new_fact"], True, [])
    (fact,) = store.current_facts("OWNS", "Ana", "billing-api")
    assert fact["valid_at"] == epoch("2026-08-17T12:00:00Z") and fact["source_ids"] == ["DOC-01"]
    assert fact["gate_id"] == res.receipt_id
    r = receipt(store, res)
    assert (r["outcome"], r["reasons"], r["predicate"], r["subject"], r["object"], r["proposer"]) == (
        "ACCEPT", ["new_fact"], "OWNS", "Ana", "billing-api", "rules")
    assert r["valid_at"] == epoch("2026-08-17T12:00:00Z") and r["id"] == res.receipt_id
    cites = store.ro("MATCH (:GateDecision {id: $g})-[:CITES]->(e:Episode) RETURN e.id", {"g": res.receipt_id})
    about = store.ro("MATCH (:GateDecision {id: $g})-[:ABOUT]->(x) RETURN x.name ORDER BY x.name", {"g": res.receipt_id})
    assert cites == [["DOC-01"]] and about == [["Ana"], ["billing-api"]]


def test_attrs_become_edge_properties_but_cannot_override_gate_fields(store):
    res = submit_fact(store, cand(attrs={"role": "Lead", "gate_id": "forged", "source_ids": ["X"]}))
    (fact,) = store.current_facts("OWNS", "Ana", "billing-api")
    assert fact["role"] == "Lead" and fact["gate_id"] == res.receipt_id and fact["source_ids"] == ["DOC-01"]


def test_attrs_outside_the_vocabulary_or_with_nested_values_are_refused_before_anything_is_written(store):
    submit_fact(store, cand())
    for attrs in ({"labels": ["Admin"]}, {":Admin": "yes"}, {"role": {"x": 1}}, {"role": ["a", {"b": 1}]}, {"note": "x" * 301},
                  {"role": None}, {"meta": {"label": "Admin"}}):
        res = submit_fact(store, adr9_owner(attrs=attrs))  # would supersede Ana
        assert (res.outcome, res.reasons, res.written) == ("REJECT", ["bad_attrs"], False)
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Ana"]  # nobody was closed
    assert count(store, "MATCH ()-[h:WAS]->() RETURN count(h)") == 0


def test_leads_accepts_a_team_or_a_project(store):
    team = submit_fact(store, cand(predicate="LEADS", object="Payments", source_ids=["DOC-06"]))
    project = submit_fact(store, cand(predicate="LEADS", object="Ledger Hardening", source_ids=["DOC-08"]))
    assert team.outcome == project.outcome == "ACCEPT"
    assert store.current_facts("LEADS", obj="Ledger Hardening", object_label="Project")[0]["subject"] == "Ana"
    assert store.current_facts("LEADS", obj="Payments")[0]["subject"] == "Ana"


def test_non_functional_predicates_accumulate(store):
    for who, src, at in (("Ana", "TCK-0001", "2026-09-23T10:00:00Z"),
                         ("Bo", "msg-0100", "2026-10-01T14:00:00Z")):  # msg-0100 is written by Bo and names billing-api
        res = submit_fact(store, cand(subject=who, predicate="WORKED_ON", source_ids=[src], valid_at=at))
        assert res.outcome == "ACCEPT"
    assert typed_edges(store, "WORKED_ON") == 2
    store.merge_entity("Episode", "DOC-10", {"ts": epoch("2026-08-21T10:00:00Z"), "channel": "doc", "author": "Cy",
                                             "title": "", "text": "Architecture. billing-api depends on auth."})
    for obj, src in (("ledger-db", "DOC-09"), ("auth", "DOC-10")):  # the source must say "depends", not only name both
        res = submit_fact(store, cand(subject="billing-api", predicate="DEPENDS_ON", object=obj,
                                      source_ids=[src], valid_at="2026-08-21T10:00:00Z"))
        assert res.outcome == "ACCEPT"
    assert typed_edges(store, "DEPENDS_ON") == 2


# ---- check 1: predicate -----------------------------------------------------------------------
def test_unknown_predicate_is_refused_with_a_receipt(store):
    res = submit_fact(store, cand(predicate="LIKES"))
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["bad_predicate"], False)
    assert receipt(store, res)["outcome"] == "REJECT" and typed_edges(store, "LIKES") == 0


def test_forbidden_predicate_is_pii_and_the_value_never_reaches_the_receipt(store):
    res = submit_fact(store, Candidate("Customer in TCK-0001", "HAS_PHONE", "+1-555-0159", "2026-09-23T10:00:00Z",
                                       ["TCK-0001"], claim_episode="TCK-0001"))
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["pii"], False)
    dump = json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)"))
    assert "555" not in dump and "HAS_PHONE" in dump
    assert receipt(store, res)["object"] == "[redacted]"


def test_malformed_valid_at_is_refused(store):
    res = submit_fact(store, cand(valid_at="yesterday-ish"))
    assert (res.outcome, res.reasons) == ("REJECT", ["bad_time"])


def test_a_name_over_200_characters_is_refused_and_not_kept_in_the_receipt(store):
    for kw in ({"subject": "A" * 10000}, {"object": "webhooks " * 1112}):
        res = submit_fact(store, cand(**kw))
        assert (res.outcome, res.reasons, res.written) == ("REJECT", ["bad_name"], False)
    longest = max(len(str(v)) for (props,) in store.ro("MATCH (g:GateDecision) RETURN properties(g)") for v in props.values())
    assert longest <= 200 and submit_fact(store, cand(subject="Ana")).outcome == "ACCEPT"


# ---- check 2: personal data in subject or object --------------------------------------------------
@pytest.mark.parametrize("value", ["+1-555-0159", "(360) 931-4245", "555 0159", "360.931.4245", "ana@example.com",
                                   "984 Marlowe Court", "12 Main Street, Springfield", "P.O. Box 77",
                                   "4111 1111 1111 1111", "4111-1111-1111-1111", "3782 822463 10005"])
def test_personal_data_in_the_object_is_refused(store, value):
    res = submit_fact(store, cand(object=value))
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["pii"], False)
    assert value not in json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)"))


def test_personal_data_in_the_subject_or_attrs_is_refused(store):
    assert submit_fact(store, cand(subject="call 360 931 4245")).reasons == ["pii"]
    assert submit_fact(store, cand(attrs={"note": "ana@example.com"})).reasons == ["pii"]


@pytest.mark.parametrize("value", ["billing-api", "ADR-0009", "Ledger Hardening", "TCK-0044", "GD-00042-12345678ab",
                                   "1790000000", "v10.2.3.4567", "4111 1111 1111 1112", "2026-09-22T15:28:00Z",
                                   "Fixed 3 bugs in 2 services", "about 600 customers", "1,234,567 rows"])
def test_ordinary_text_is_not_personal_data(value):
    assert detect_pii(value) == []


@pytest.mark.parametrize("filler", ["1", "1.", "a", "a-", "ab ", "a@", "1 "])
def test_detect_pii_stays_fast_on_long_input(filler):
    import time

    text = filler * (20000 // len(filler))
    started = time.perf_counter()
    detect_pii(text)
    assert time.perf_counter() - started < 0.25  # the patterns must not backtrack quadratically


def test_detect_pii_names_the_kind():
    assert detect_pii("mail ana@example.com") == ["email"]
    assert detect_pii("call +1-555-0159 now") == ["phone"]
    assert detect_pii("lives at 984 Marlowe Court") == ["address"]
    assert detect_pii("card 4111 1111 1111 1111") == ["card"]


@pytest.mark.parametrize("value, kind", [("4111.1111.1111.1111", "card"), ("ana [at] example [dot] com", "email"),
                                         ("ana(at)example.com", "email"), ("ANA {AT} EXAMPLE.COM", "email")])
def test_dotted_cards_and_spelled_out_emails_are_personal_data(value, kind):
    assert detect_pii(value) == [kind]


@pytest.mark.parametrize("value", ["3.14159265358979323", "10.2.3.4567", "values[at] = 3", "array(at) is a method.", "4111.1111.1111.1112"])
def test_decimals_versions_and_code_are_not_personal_data(value):
    assert detect_pii(value) == []


# ---- check 3: a source is required --------------------------------------------------------------
def test_no_source_is_refused(store):
    res = submit_fact(store, cand(source_ids=[]))
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["no_source"], False)


def test_no_source_that_contradicts_the_present_also_reports_the_conflict(store):
    assert submit_fact(store, cand()).outcome == "ACCEPT"
    res = submit_fact(store, cand(subject="Payments", source_ids=[], claim_episode="msg-0100"))
    assert (res.outcome, res.reasons) == ("REJECT", ["no_source", "conflicts_current"])
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Ana"]


def test_claim_episode_is_linked_when_it_is_not_a_source(store):
    res = submit_fact(store, cand(subject="Bo", source_ids=[], claim_episode="msg-0100"))
    r = receipt(store, res)
    assert r["claim_episode"] == "msg-0100"
    assert store.ro("MATCH (:GateDecision {id: $g})-[:CLAIMED_IN]->(e) RETURN e.id", {"g": res.receipt_id}) == [["msg-0100"]]
    assert store.ro("MATCH (:GateDecision {id: $g})-[:CITES]->(e) RETURN e.id", {"g": res.receipt_id}) == []


# ---- check 4: sources must exist ------------------------------------------------------------------
def test_missing_source_episode_is_refused(store):
    assert submit_fact(store, cand(source_ids=["DOC-99"])).reasons == ["source_not_found"]
    assert submit_fact(store, cand(source_ids=["DOC-01", "DOC-99"])).reasons == ["source_not_found"]


def test_cites_only_episodes_that_exist(store):
    res = submit_fact(store, cand(source_ids=["DOC-01", "DOC-99"]))
    assert res.outcome == "REJECT"
    assert store.ro("MATCH (:GateDecision {id: $g})-[:CITES]->(e) RETURN e.id", {"g": res.receipt_id}) == [["DOC-01"]]
    assert receipt(store, res)["source_ids"] == ["DOC-01", "DOC-99"]


def test_a_source_dated_after_the_claim_it_supports_is_refused(store):
    late = submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0004"],
                                   valid_at="2026-09-24T10:00:00Z", claim_episode="TCK-0001"))  # claim read 09-23, source of 09-24
    assert (late.outcome, late.reasons, late.written) == ("REJECT", ["source_after_claim"], False)
    ok = submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0004"],
                                 valid_at="2026-09-24T10:00:00Z", claim_episode="TCK-0005"))
    assert ok.outcome == "ACCEPT"
    assert typed_edges(store, "WORKED_ON") == 1


# ---- check 5: a cited episode mentions both ends ---------------------------------------------------
def test_source_that_names_only_one_end_is_ungrounded(store):
    res = submit_fact(store, cand(object="auth", source_ids=["DOC-03"]))
    assert (res.outcome, res.reasons) == ("REJECT", ["ungrounded"])


def test_names_match_whole_words_so_author_does_not_ground_auth(store):
    assert submit_fact(store, cand(object="auth", source_ids=["DOC-04"])).reasons == ["ungrounded"]


def test_grounding_is_case_insensitive_and_counts_the_author(store):
    assert submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", object="billing-api",
                                   source_ids=["TCK-0001"], valid_at="2026-09-23T10:00:00Z")).outcome == "ACCEPT"
    store.merge_entity("Episode", "msg-0200", {"ts": epoch("2026-08-17T12:00:00Z"), "author": "Cy", "title": "",
                                               "text": "ANA OWNS BILLING-API"})
    assert submit_fact(store, cand(source_ids=["msg-0200"])).outcome == "ACCEPT"


def test_a_chat_channel_name_does_not_ground_a_fact_but_a_ticket_title_does(store):
    store.merge_entity("Episode", "msg-0300", {"ts": 1, "channel": "chat", "author": "Ana", "title": "#billing-api",
                                               "text": "Lunch is at noon."})
    store.merge_entity("Episode", "TCK-0300", {"ts": 1, "channel": "ticket", "author": "Ana", "title": "billing-api retries",
                                               "text": "Lunch is at noon."})
    chat = submit_fact(store, cand(predicate="WORKED_ON", source_ids=["msg-0300"], valid_at="1970-01-01T00:00:01Z"))
    ticket = submit_fact(store, cand(predicate="WORKED_ON", source_ids=["TCK-0300"], valid_at="1970-01-01T00:00:01Z"))
    assert (chat.reasons, ticket.outcome) == (["ungrounded"], "ACCEPT")


def test_both_ends_must_appear_in_one_episode_not_spread_over_two(store):
    store.merge_entity("Episode", "msg-0201", {"ts": 1, "author": "Cy", "title": "", "text": "Ana is busy."})
    store.merge_entity("Episode", "msg-0202", {"ts": 2, "author": "Cy", "title": "", "text": "auth is flaky."})
    assert submit_fact(store, cand(object="auth", source_ids=["msg-0201", "msg-0202"])).reasons == ["ungrounded"]


def test_names_in_the_same_line_must_come_with_the_relation_word(store):
    store.merge_entity("Episode", "msg-0500", {"ts": epoch("2026-10-02T10:00:00Z"), "channel": "chat", "author": "Ana", "title": "#platform",
                                               "text": "Wrapped up tests for session expiry in auth, merging after lunch."})
    assert submit_fact(store, cand(subject="Cy", object="auth", source_ids=["DOC-01"])).outcome == "ACCEPT"
    owns = submit_fact(store, cand(object="auth", source_ids=["msg-0500"], valid_at="2026-10-02T10:00:00Z"))
    assert (owns.outcome, owns.reasons) == ("REJECT", ["ungrounded"])  # names both, says nothing about ownership
    assert [f["subject"] for f in store.current_facts("OWNS", obj="auth")] == ["Cy"]
    worked = submit_fact(store, cand(predicate="WORKED_ON", object="auth", source_ids=["msg-0500"], valid_at="2026-10-02T10:00:00Z"))
    assert worked.outcome == "ACCEPT"  # for WORKED_ON the author and the system are enough


def test_the_author_of_a_catalog_does_not_become_a_party_to_every_line(store):
    store.merge_entity("Episode", "DOC-11", {"ts": epoch("2026-08-17T12:00:00Z"), "channel": "doc", "author": "Cy", "title": "Service catalog",
                                             "text": "- billing-api: public API. Owner: Ana (Payments).\n- ledger-db: ledger. Owner: Bo (Platform)."})
    assert submit_fact(store, cand(subject="Cy", source_ids=["DOC-11"])).reasons == ["ungrounded"]  # Cy only wrote the page
    assert submit_fact(store, cand(subject="Bo", source_ids=["DOC-11"])).reasons == ["ungrounded"]  # Bo owns the other line
    assert submit_fact(store, cand(source_ids=["DOC-11"])).outcome == "ACCEPT"


def test_a_first_person_line_names_its_author(store):
    store.merge_entity("Episode", "msg-0600", {"ts": epoch("2026-10-02T10:00:00Z"), "channel": "chat", "author": "Cy", "title": "#platform",
                                               "text": "I am now the owner of billing-api."})
    assert submit_fact(store, cand(subject="Cy", source_ids=["msg-0600"], valid_at="2026-10-02T10:00:00Z")).outcome == "ACCEPT"
    other = submit_fact(store, cand(subject="Bo", source_ids=["msg-0600"], valid_at="2026-10-02T10:00:00Z"))
    assert other.reasons == ["ungrounded"]  # the speaker speaks for himself only


def test_names_on_different_lines_do_not_make_a_statement(store):
    store.merge_entity("Episode", "DOC-12", {"ts": epoch("2026-08-17T12:00:00Z"), "channel": "doc", "author": "Cy", "title": "",
                                             "text": "Ana is on call.\nbilling-api is noisy.\nOwnership review is due."})
    assert submit_fact(store, cand(source_ids=["DOC-12"])).reasons == ["ungrounded"]


# ---- check 6: entities resolve with the right label ---------------------------------------------------
def test_unknown_entity_is_refused_and_never_created(store):
    res = submit_fact(store, cand(subject="Zed", object="auth", source_ids=["DOC-05"]))
    assert (res.outcome, res.reasons) == ("REJECT", ["unknown_entity"])
    assert not store.find_entity("Person", "Zed")


def test_entity_of_the_wrong_label_is_unknown_for_that_predicate(store):
    res = submit_fact(store, cand(subject="Payments", object="billing-api", source_ids=["DOC-01"]))
    assert (res.outcome, res.reasons) == ("REJECT", ["unknown_entity"])
    about = store.ro("MATCH (:GateDecision {id: $g})-[:ABOUT]->(x) RETURN x.name ORDER BY x.name", {"g": res.receipt_id})
    assert about == [["Payments"], ["billing-api"]]  # a refusal still points at what it was about


# ---- check 7: CONFIRM -----------------------------------------------------------------------------------
def test_same_fact_confirms_and_adds_the_source(store):
    first = submit_fact(store, cand())
    res = submit_fact(store, cand(source_ids=["msg-0100"], valid_at="2026-10-01T14:00:00Z",
                                  subject="Ana", object="billing-api"))
    assert (res.outcome, res.reasons, res.written) == ("CONFIRM", ["already_current"], True)
    (fact,) = store.current_facts("OWNS", "Ana", "billing-api")
    assert fact["source_ids"] == ["DOC-01", "msg-0100"] and fact["gate_id"] == first.receipt_id
    assert fact["valid_at"] == epoch("2026-08-17T12:00:00Z")  # the fact keeps its original start
    assert typed_edges(store, "OWNS") == 1


def test_confirming_with_a_known_source_changes_nothing(store):
    submit_fact(store, cand())
    res = submit_fact(store, cand())
    assert (res.outcome, res.written) == ("CONFIRM", False)
    assert store.current_facts("OWNS", "Ana", "billing-api")[0]["source_ids"] == ["DOC-01"]


def test_an_exact_repeat_is_counted_on_one_receipt_and_does_not_grow_the_graph(store):
    submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0001"], valid_at="2026-09-23T10:00:00Z"))
    first = submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0001"], valid_at="2026-09-23T10:00:00Z"))
    nodes, edges = count(store, "MATCH (n) RETURN count(n)"), count(store, "MATCH ()-[r]->() RETURN count(r)")
    again = [submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0001"], valid_at="2026-09-23T10:00:00Z"))
             for _ in range(48)]
    assert {r.receipt_id for r in again} == {first.receipt_id} and {(r.outcome, r.written) for r in again} == {("CONFIRM", False)}
    assert (count(store, "MATCH (n) RETURN count(n)"), count(store, "MATCH ()-[r]->() RETURN count(r)")) == (nodes, edges)
    assert receipt(store, first)["repeats"] == 49
    (fact,) = store.current_facts("WORKED_ON", "Ana", "billing-api")
    assert fact["count"] == 1 and fact["source_ids"] == ["TCK-0001"]
    # a different proposer, or a source the edge does not have yet, is not a repeat
    other = submit_fact(store, Candidate("Ana", "WORKED_ON", "billing-api", "2026-09-23T10:00:00Z", ["TCK-0001"], proposer="llm"))
    new_src = submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0004"], valid_at="2026-09-24T10:00:00Z"))
    assert other.receipt_id != first.receipt_id and (new_src.outcome, new_src.written) == ("CONFIRM", True)
    assert store.current_facts("WORKED_ON", "Ana", "billing-api")[0]["count"] == 2


def test_worked_on_count_follows_the_distinct_sources(store):
    items = [("TCK-0001", "2026-09-23T10:00:00Z"), ("TCK-0004", "2026-09-24T10:00:00Z"),
             ("TCK-0005", "2026-09-25T10:00:00Z"), ("TCK-0004", "2026-09-24T10:00:00Z")]
    for sid, ts in items:
        assert submit_fact(store, cand(predicate="WORKED_ON", source_ids=[sid], valid_at=ts)).outcome in ("ACCEPT", "CONFIRM")
    (fact,) = store.current_facts("WORKED_ON", "Ana", "billing-api")
    assert fact["count"] == 3 and fact["source_ids"] == ["TCK-0001", "TCK-0004", "TCK-0005"]
    assert fact["valid_at"] == epoch("2026-09-23T10:00:00Z")


# ---- check 8: functional predicates -------------------------------------------------------------------------
def adr9_owner(**kw):
    return cand(subject="Bo", source_ids=["ADR-0009"], valid_at="2026-09-22T15:28:00Z", **kw)


def test_newer_owner_supersedes_and_history_keeps_the_old_one(store):
    submit_fact(store, cand())
    res = submit_fact(store, adr9_owner())
    assert (res.outcome, res.reasons, res.written) == ("SUPERSEDE", ["newer_than_current"], True)
    (closed,) = res.closed
    assert (closed["predicate"], closed["subject"], closed["object"]) == ("OWNS", "Ana", "billing-api")
    assert closed["invalid_at"] == epoch("2026-09-22T15:28:00Z") and closed["source_ids"] == ["DOC-01"]
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Bo"]
    hist = store.ro("MATCH (p:Person)-[h:WAS]->(s:System) RETURN p.name, h.predicate, h.valid_at, h.invalid_at, "
                    "h.closed_by, h.source_ids")
    assert hist == [["Ana", "OWNS", epoch("2026-08-17T12:00:00Z"), epoch("2026-09-22T15:28:00Z"),
                     res.receipt_id, ["DOC-01"]]]
    assert store.current_facts("OWNS", "Bo", "billing-api")[0]["gate_id"] == res.receipt_id


def test_older_claim_is_refused_and_the_present_stays(store):
    submit_fact(store, cand())
    submit_fact(store, adr9_owner())
    before = count(store, "MATCH ()-[h:WAS]->() RETURN count(h)")
    res = submit_fact(store, cand(source_ids=["DOC-01"], valid_at="2026-08-27T20:09:00Z", claim_episode="msg-0100"))
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["older_than_current"], False)
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Bo"]
    assert count(store, "MATCH ()-[h:WAS]->() RETURN count(h)") == before


def test_a_claim_at_exactly_the_current_start_is_not_newer(store):
    submit_fact(store, adr9_owner())
    res = submit_fact(store, cand(subject="Ana", source_ids=["ADR-0009"], valid_at="2026-09-22T15:28:00Z"))
    assert (res.outcome, res.reasons) == ("REJECT", ["older_than_current"])


def test_a_claim_cannot_be_newer_than_the_sources_that_state_it(store):
    submit_fact(store, cand())
    forged = submit_fact(store, cand(subject="Bo", source_ids=["ADR-0009"], valid_at="2030-01-01T00:00:00Z"))
    assert (forged.outcome, forged.reasons, forged.written) == ("REJECT", ["valid_after_source"], False)
    # a newer source that does not state the claim does not help: TCK-0005 names neither Bo nor an owner
    padded = submit_fact(store, cand(subject="Bo", source_ids=["ADR-0009", "TCK-0005"], valid_at="2026-09-25T10:00:00Z"))
    assert (padded.outcome, padded.reasons) == ("REJECT", ["valid_after_source"])
    later = submit_fact(store, cand(subject="Ana", predicate="WORKED_ON", source_ids=["TCK-0001"], valid_at="2030-01-01T00:00:00Z"))
    assert later.reasons == ["valid_after_source"] and typed_edges(store, "WORKED_ON") == 0
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Ana"]
    again = submit_fact(store, cand(valid_at="2030-01-01T00:00:00Z"))  # a fact that is already current is only confirmed
    assert again.outcome == "CONFIRM"


def test_a_closed_fact_comes_back_only_on_new_evidence(store):
    submit_fact(store, cand())
    submit_fact(store, adr9_owner())  # Ana's ownership is closed on 2026-09-22
    store.merge_entity("Episode", "msg-0400", {"ts": epoch("2026-10-02T09:00:00Z"), "channel": "chat", "author": "Bo", "title": "#support",
                                               "text": "Quoting the runbook: Ana owns billing-api."})
    store.link("Episode", "msg-0400", "REFERS_TO", "Episode", ["DOC-01"])
    relay = submit_fact(store, cand(source_ids=["msg-0400"], valid_at="2026-10-02T09:00:00Z"))
    assert (relay.outcome, relay.reasons, relay.written) == ("REJECT", ["closed_fact"], False)
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Bo"]
    # the same words in a message that points at nothing old are new evidence: the gate cannot tell the difference
    fresh = submit_fact(store, cand(source_ids=["msg-0100"], valid_at="2026-10-01T14:00:00Z"))
    assert (fresh.outcome, fresh.reasons) == ("SUPERSEDE", ["newer_than_current"])


def test_functional_on_the_subject_side(store):
    submit_fact(store, cand(predicate="MEMBER_OF", object="Payments", source_ids=["DOC-06"],
                            valid_at="2026-08-17T13:00:00Z"))
    res = submit_fact(store, cand(predicate="MEMBER_OF", object="Platform", source_ids=["DOC-07"],
                                  valid_at="2026-09-01T10:00:00Z"))
    assert res.outcome == "SUPERSEDE" and res.closed[0]["object"] == "Payments"
    assert [f["object"] for f in store.current_facts("MEMBER_OF", "Ana")] == ["Platform"]
    late = submit_fact(store, cand(predicate="MEMBER_OF", object="Payments", source_ids=["DOC-03"],
                                   valid_at="2026-08-18T10:00:00Z"))
    assert late.reasons == ["older_than_current"]


def test_supersedes_marks_the_old_decision(store):
    res = submit_fact(store, Candidate("ADR-0011", "SUPERSEDES", "ADR-0004", "2026-09-10T14:00:00Z", ["PM-03"]))
    assert res.outcome == "ACCEPT"
    status = dict(store.ro("MATCH (d:Decision) RETURN d.id, d.status"))
    assert status["ADR-0004"] == "superseded" and status.get("ADR-0011") is None


def test_a_superseded_decision_cannot_supersede(store):
    assert submit_fact(store, Candidate("ADR-0011", "SUPERSEDES", "ADR-0004", "2026-09-10T14:00:00Z", ["PM-03"])).outcome == "ACCEPT"
    back = submit_fact(store, Candidate("ADR-0004", "SUPERSEDES", "ADR-0011", "2026-09-10T14:00:00Z", ["PM-03"]))
    assert (back.outcome, back.reasons, back.written) == ("REJECT", ["superseded_decision"], False)
    status = dict(store.ro("MATCH (d:Decision) RETURN d.id, d.status"))
    assert status["ADR-0004"] == "superseded" and status.get("ADR-0011") is None
    assert typed_edges(store, "SUPERSEDES") == 1


# ---- order of checks ---------------------------------------------------------------------------------------
def test_the_first_failing_check_decides(store):
    assert submit_fact(store, cand(object="ana@example.com", source_ids=[])).reasons == ["pii"]
    assert submit_fact(store, cand(predicate="HAS_PHONE", source_ids=[])).reasons == ["pii"]
    assert submit_fact(store, cand(source_ids=["DOC-99"], subject="Zed")).reasons == ["source_not_found"]
    assert submit_fact(store, cand(subject="Zed", object="auth", source_ids=["DOC-03"])).reasons == ["ungrounded"]


# ---- receipts -------------------------------------------------------------------------------------------------
def replay(store):
    return [submit_fact(store, c).receipt_id for c in (
        cand(), cand(source_ids=[]), cand(object="ana@example.com"), adr9_owner(), cand(subject="Zed"),
        cand(source_ids=["TCK-0001"], valid_at="2026-09-23T10:00:00Z"))]


def test_receipt_ids_repeat_across_identical_replays(store):
    first = replay(store)
    other = seed(Store(store.db, store.tenant + "_b"))
    try:
        second = replay(other)
    finally:
        other.drop()
    assert first == second and len(set(first)) == len(first)
    assert all(rid.startswith("GD-") for rid in first)


def test_receipt_id_depends_on_the_candidate(store):
    a = submit_fact(store, cand(source_ids=[]))
    other = seed(Store(store.db, store.tenant + "_c"))
    try:
        b = submit_fact(other, cand(source_ids=[], subject="Bo"))
    finally:
        other.drop()
    assert a.receipt_id != b.receipt_id and a.receipt_id.split("-")[1] == b.receipt_id.split("-")[1]


def test_receipt_time_is_the_latest_episode_involved_unless_given(store):
    res = submit_fact(store, cand(source_ids=["DOC-01"], claim_episode="msg-0100", subject="Bo"))
    assert receipt(store, res)["ts"] == epoch("2026-10-01T14:00:00Z")
    res = submit_fact(store, cand(source_ids=["DOC-01"]), at="2026-10-05T00:00:00Z")
    assert receipt(store, res)["ts"] == epoch("2026-10-05T00:00:00Z")
    res = submit_fact(store, cand(source_ids=[], subject="Bo"))
    assert receipt(store, res)["ts"] == epoch("2026-08-17T12:00:00Z")  # nothing else to go by: the fact's own time


def test_a_refusal_writes_only_the_receipt(store):
    nodes = count(store, "MATCH (n) RETURN count(n)")
    res = submit_fact(store, cand(source_ids=[]))
    assert res.outcome == "REJECT"
    assert count(store, "MATCH (n) RETURN count(n)") == nodes + 1 + 1  # the receipt and the sequence counter
    assert typed_edges(store, "OWNS") == 0


# ---- findings --------------------------------------------------------------------------------------------------
def finding(store, **kw):
    args = dict(agent="agent:support-bot", session="s-1", text="billing-api retries are idempotent.",
                about=["billing-api"], source_ids=["DOC-01"], at="2026-10-02T09:00:00Z")
    return submit_finding(store, **{**args, **kw})


def test_finding_is_kept_with_its_provenance(store):
    res = finding(store, about=["billing-api", "Ana"], source_ids=["DOC-01", "TCK-0001"])
    assert (res.outcome, res.reasons, res.written) == ("ACCEPT", ["new_finding"], True) and res.record_id
    (row,) = store.ro("MATCH (a:Agent)-[:WROTE]->(f:Finding {id: $f}) RETURN a.name, f.text, f.ts, f.session, f.gate_id",
                      {"f": res.record_id})
    assert row == ["agent:support-bot", "billing-api retries are idempotent.", epoch("2026-10-02T09:00:00Z"), "s-1",
                   res.receipt_id]
    about = store.ro("MATCH (:Finding {id: $f})-[:ABOUT]->(x) RETURN x.name ORDER BY x.name", {"f": res.record_id})
    src = store.ro("MATCH (:Finding {id: $f})-[:DERIVED_FROM]->(e:Episode) RETURN e.id ORDER BY e.id", {"f": res.record_id})
    assert about == [["Ana"], ["billing-api"]] and src == [["DOC-01"], ["TCK-0001"]]
    assert receipt(store, res)["predicate"] == "FINDING"


def test_finding_may_be_derived_from_a_receipt(store):
    gate = submit_fact(store, cand())
    res = finding(store, source_ids=[gate.receipt_id])
    assert res.outcome == "ACCEPT"
    got = store.ro("MATCH (:Finding {id: $f})-[:DERIVED_FROM]->(g:GateDecision) RETURN g.id", {"f": res.record_id})
    assert got == [[gate.receipt_id]]


def test_a_refused_receipt_is_not_a_source(store):
    refused = submit_fact(store, cand(source_ids=[]))
    assert refused.outcome == "REJECT"
    res = finding(store, source_ids=[refused.receipt_id])
    assert (res.outcome, res.reasons, res.written) == ("REJECT", ["source_rejected"], False)
    assert handoff(store, source_ids=[refused.receipt_id]).reasons == ["source_rejected"]
    assert count(store, "MATCH (f:Finding) RETURN count(f)") == 0 and count(store, "MATCH (h:Handoff) RETURN count(h)") == 0


def test_a_finding_or_handoff_cannot_predate_its_sources(store):
    assert finding(store, at="2026-08-01T00:00:00Z").reasons == ["source_after_claim"]  # DOC-01 is from 2026-08-17
    assert handoff(store, at="2026-08-01T00:00:00Z").reasons == ["source_after_claim"]  # TCK-0001 is from 2026-09-23
    assert finding(store, at="2026-08-17T12:00:00Z").outcome == "ACCEPT"  # the same minute as the source is fine


def test_finding_with_an_unknown_entity_is_refused(store):
    res = finding(store, about=["billing-api", "no-such-system"])
    assert (res.outcome, res.reasons, res.written, res.record_id) == ("REJECT", ["unknown_entity"], False, None)
    assert count(store, "MATCH (f:Finding) RETURN count(f)") == 0 and receipt(store, res)["outcome"] == "REJECT"


def test_finding_needs_something_to_be_about(store):
    assert finding(store, about=[]).reasons == ["no_about"]


def test_finding_without_a_known_source_is_refused(store):
    assert finding(store, source_ids=[]).reasons == ["no_source"]
    assert finding(store, source_ids=["DOC-99"]).reasons == ["source_not_found"]
    assert finding(store, source_ids=["DOC-01", "DOC-99"]).outcome == "ACCEPT"  # one known source is enough


def test_finding_with_personal_data_is_refused_and_not_echoed(store):
    res = finding(store, text="Call the customer on +1-555-0159 about billing-api.")
    assert (res.outcome, res.reasons) == ("REJECT", ["pii"])
    assert "555" not in json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)"))
    assert count(store, "MATCH (a:Agent) RETURN count(a)") == 0  # a refusal creates nothing but its receipt


def test_personal_data_in_the_session_label_or_agent_name_is_refused(store):
    for kw in ({"session": "555.015.9999"}, {"agent": "agent:555-015-9999"}):
        res = finding(store, **kw)
        assert (res.outcome, res.reasons) == ("REJECT", ["pii"])
    dump = json.dumps(store.ro("MATCH (n) RETURN properties(n)"))
    assert "555.015.9999" not in dump and "555-015-9999" not in dump and count(store, "MATCH (a:Agent) RETURN count(a)") == 0


def test_finding_needs_a_well_formed_agent_name(store):
    for agent in ("agent:x'}) DETACH DELETE n //", "ops", "agent:", "agent:" + "a" * 5000, "agent:Ops", "agent:ops\n"):
        res = finding(store, agent=agent)
        assert (res.outcome, res.reasons) == ("REJECT", ["bad_agent"])
    assert count(store, "MATCH (a:Agent) RETURN count(a)") == 0 and count(store, "MATCH (f:Finding) RETURN count(f)") == 0
    assert max(len(str(v)) for (props,) in store.ro("MATCH (g:GateDecision) RETURN properties(g)") for v in props.values()) <= 200


def test_finding_time_must_parse(store):
    assert finding(store, at="not a time").reasons == ["bad_time"]


# ---- handoffs ----------------------------------------------------------------------------------------------------
def handoff(store, **kw):
    args = dict(from_="agent:support-bot", to="Bo", topic="duplicate invoice emails",
                note="Customers report duplicates since this morning.", source_ids=["TCK-0001"],
                at="2026-10-02T09:00:00Z")
    return submit_handoff(store, **{**args, **kw})


def test_handoff_writes_parties_and_sources(store):
    res = handoff(store, source_ids=["TCK-0001", "DOC-01"])
    assert (res.outcome, res.reasons, res.written) == ("ACCEPT", ["new_handoff"], True) and res.record_id
    assert store.find_entity("Agent", "agent:support-bot")  # created on demand
    (row,) = store.ro("MATCH (h:Handoff {id: $h})-[:FROM]->(a), (h)-[:TO]->(b) RETURN a.name, b.name, h.topic, h.ts, h.gate_id",
                      {"h": res.record_id})
    assert row == ["agent:support-bot", "Bo", "duplicate invoice emails", epoch("2026-10-02T09:00:00Z"), res.receipt_id]
    cites = store.ro("MATCH (:Handoff {id: $h})-[:CITES]->(e:Episode) RETURN e.id ORDER BY e.id", {"h": res.record_id})
    assert cites == [["DOC-01"], ["TCK-0001"]]


def test_handoff_between_a_team_and_a_person(store):
    res = handoff(store, from_="Payments", to="Ana")
    assert res.outcome == "ACCEPT" and not store.find_entity("Agent", "Payments")
    assert store.ro("MATCH (:Handoff {id: $h})-[:FROM]->(t:Team) RETURN t.name", {"h": res.record_id}) == [["Payments"]]


def test_handoff_refusals(store):
    assert handoff(store, to="Nobody").reasons == ["unknown_entity"]
    assert handoff(store, from_="Nobody").reasons == ["unknown_entity"]
    assert handoff(store, source_ids=[]).reasons == ["no_source"]
    assert handoff(store, source_ids=["DOC-99"]).reasons == ["source_not_found"]
    bad = handoff(store, note="Reach them at ana@example.com")
    assert bad.reasons == ["pii"] and "example.com" not in json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)"))
    assert count(store, "MATCH (h:Handoff) RETURN count(h)") == 0
    assert not store.find_entity("Agent", "agent:support-bot")  # refused handoffs do not create agents


def test_personal_data_in_a_party_name_is_refused(store):
    res = handoff(store, from_="agent:555-015-9999")
    assert (res.outcome, res.reasons) == ("REJECT", ["pii"])
    assert "555-015-9999" not in json.dumps(store.ro("MATCH (n) RETURN properties(n)"))
    assert count(store, "MATCH (a:Agent) RETURN count(a)") == 0


def test_a_handoff_never_creates_an_agent_with_a_malformed_name(store):
    for name in ("ghost", "agent:", "agent:Ghost'}) DETACH DELETE n //", "agent:" + "a" * 5000, "Agent:ghost", "agent:ghost\n"):
        for kw in ({"to": name}, {"from_": name}):
            res = handoff(store, **kw)
            assert (res.outcome, res.reasons) == ("REJECT", ["unknown_entity"])
    assert count(store, "MATCH (a:Agent) RETURN count(a)") == 0 and count(store, "MATCH (h:Handoff) RETURN count(h)") == 0
    assert handoff(store, to="agent:ghost-2").outcome == "ACCEPT"  # a well-formed id is still created on first use


def test_agent_names_are_created_only_with_the_agent_prefix(store):
    assert handoff(store, to="support-bot").reasons == ["unknown_entity"]

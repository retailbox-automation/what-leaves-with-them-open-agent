"""Write gate, CLOSE path: ending a fact without a replacement (a person's last day), on every FalkorDB in HM_TEST_PORTS."""

import json
import os

import pytest

from honest_memory.gate import submit_end, submit_fact, submit_handoff
from honest_memory.store import Store, epoch
from tests.test_gate import cand, receipt, seed, typed_edges

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]
LAST_DAY = "2026-10-08T17:00:00Z"
END_EPISODES = [  # id, ts, channel, author, text
    ("DOC-20", LAST_DAY, "doc", "Cy",
     "Last day: Ana. Ana is no longer a member of the Payments team. Ana no longer owns billing-api. "
     "Ana no longer leads Ledger Hardening."),
    ("DOC-21", LAST_DAY, "doc", "Cy", "Last day: Ana. Nothing else is listed."),
    ("DOC-22", "2026-11-02T10:00:00Z", "doc", "Cy", "Ana rejoined; Ana owns billing-api again."),
]


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def store(request):
    from falkordb import FalkorDB

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=request.param, socket_timeout=10)
    s = seed(Store(db, f"test_gate_close_{os.getpid()}"))
    for eid, ts, channel, author, text in END_EPISODES:
        s.merge_entity("Episode", eid, {"ts": epoch(ts), "channel": channel, "author": author, "title": "", "text": text})
    assert submit_fact(s, cand()).outcome == "ACCEPT"                       # Ana OWNS billing-api, DOC-01
    assert submit_fact(s, cand(predicate="MEMBER_OF", object="Payments", source_ids=["DOC-06"],
                               valid_at="2026-08-17T13:00:00Z")).outcome == "ACCEPT"
    assert submit_fact(s, cand(predicate="LEADS", object="Ledger Hardening", source_ids=["DOC-08"],
                               valid_at="2026-08-18T14:00:00Z")).outcome == "ACCEPT"
    yield s
    s.drop()


def end(store, predicate="OWNS", obj="billing-api", sources=("DOC-20",), at=LAST_DAY, subject="Ana"):
    return submit_end(store, subject, predicate, obj, list(sources), at)


def history(store, predicate, obj):
    return store.ro("MATCH (a {name: 'Ana'})-[h:WAS]->(b {name: $o}) WHERE h.predicate = $p "
                    "RETURN h.valid_at, h.invalid_at, h.source_ids, h.closed_by", {"o": obj, "p": predicate})


# ---- CLOSE -----------------------------------------------------------------------------------------------
def test_end_moves_the_current_edge_to_history_with_a_close_receipt(store):
    res = end(store)
    assert (res.outcome, res.reasons, res.written) == ("CLOSE", ["ended"], True)
    assert res.closed == [{"predicate": "OWNS", "subject": "Ana", "object": "billing-api",
                           "valid_at": epoch("2026-08-17T12:00:00Z"), "invalid_at": epoch(LAST_DAY),
                           "source_ids": ["DOC-01"]}]
    assert store.current_facts("OWNS", obj="billing-api") == [] and typed_edges(store, "OWNS") == 0
    assert history(store, "OWNS", "billing-api") == [[epoch("2026-08-17T12:00:00Z"), epoch(LAST_DAY), ["DOC-01"],
                                                      res.receipt_id]]
    r = receipt(store, res)
    assert (r["outcome"], r["reasons"], r["predicate"], r["subject"], r["object"]) == (
        "CLOSE", ["ended"], "OWNS", "Ana", "billing-api")
    assert r["ts"] == r["valid_at"] == epoch(LAST_DAY) and r["source_ids"] == ["DOC-20"]
    cites = store.ro("MATCH (:GateDecision {id: $g})-[:CITES]->(e:Episode) RETURN e.id", {"g": res.receipt_id})
    about = store.ro("MATCH (:GateDecision {id: $g})-[:ABOUT]->(x) RETURN x.name ORDER BY x.name", {"g": res.receipt_id})
    assert cites == [["DOC-20"]] and about == [["Ana"], ["billing-api"]]


def test_membership_and_project_lead_end_the_same_way(store):
    team = end(store, "MEMBER_OF", "Payments")
    project = end(store, "LEADS", "Ledger Hardening")
    assert team.outcome == project.outcome == "CLOSE"
    assert typed_edges(store, "MEMBER_OF") == typed_edges(store, "LEADS") == 0
    assert history(store, "LEADS", "Ledger Hardening")[0][3] == project.receipt_id


# ---- the same checks as every other write ------------------------------------------------------------------
@pytest.mark.parametrize("kwargs,reasons", [
    ({"sources": ()}, ["no_source"]),
    ({"sources": ("DOC-99",)}, ["source_not_found"]),
    ({"sources": ("DOC-21",)}, ["ungrounded"]),                 # names Ana, not billing-api
    ({"obj": "billing-apiv2", "sources": ("DOC-20",)}, ["ungrounded"]),
    ({"predicate": "LIKES"}, ["bad_predicate"]),
    ({"predicate": "HAS_PHONE"}, ["pii"]),
    ({"obj": "+1-555-0159"}, ["pii"]),
    ({"at": "the end of the month"}, ["bad_time"]),
])
def test_end_refusals(store, kwargs, reasons):
    res = end(store, **kwargs)
    assert (res.outcome, res.reasons, res.written) == ("REJECT", reasons, False)
    assert typed_edges(store, "OWNS") == 1 and receipt(store, res)["outcome"] == "REJECT"


def test_end_of_an_unknown_entity_is_refused(store):
    store.merge_entity("Episode", "DOC-23", {"ts": epoch(LAST_DAY), "channel": "doc", "author": "Cy", "title": "",
                                             "text": "Zed no longer owns billing-api."})
    assert end(store, subject="Zed", sources=("DOC-23",)).reasons == ["unknown_entity"]


def test_nothing_to_end_is_refused(store):
    store.merge_entity("Episode", "DOC-24", {"ts": epoch(LAST_DAY), "channel": "doc", "author": "Cy", "title": "",
                                             "text": "Bo no longer owns billing-api."})  # a proper end record, but Bo owns nothing
    res = end(store, subject="Bo", sources=("DOC-24",))
    assert (res.outcome, res.reasons) == ("REJECT", ["not_current"])


def test_an_end_dated_before_the_fact_began_is_refused(store):
    res = end(store, at="2026-08-01T00:00:00Z")
    assert (res.outcome, res.reasons) == ("REJECT", ["older_than_current"])
    assert typed_edges(store, "OWNS") == 1


def test_ending_twice_is_refused_and_history_keeps_one_row(store):
    assert end(store).outcome == "CLOSE"
    again = end(store)
    assert (again.outcome, again.reasons) == ("REJECT", ["already_ended"])
    assert len(history(store, "OWNS", "billing-api")) == 1


def test_replaying_the_old_evidence_does_not_reopen_an_ended_fact(store):
    assert end(store).outcome == "CLOSE"
    replay = submit_fact(store, cand())               # the catalog again, valid 2026-08-17, ended 2026-10-08
    assert (replay.outcome, replay.reasons, replay.written) == ("REJECT", ["already_ended"], False)
    assert typed_edges(store, "OWNS") == 0


def test_newer_evidence_after_the_end_is_a_new_fact(store):
    assert end(store).outcome == "CLOSE"
    back = submit_fact(store, cand(source_ids=["DOC-22"], valid_at="2026-11-02T10:00:00Z"))
    assert (back.outcome, back.reasons) == ("ACCEPT", ["new_fact"])
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Ana"]


def test_an_end_never_stores_personal_data(store):
    end(store, obj="+1-555-0159")
    assert "555" not in json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)"))


# ---- proposed handoffs ------------------------------------------------------------------------------------------
def test_a_handoff_can_be_written_as_proposed(store):
    res = submit_handoff(store, "Ana", "Bo", "billing-api", "Take over billing-api.", ["DOC-20"], LAST_DAY,
                         status="proposed")
    assert res.outcome == "ACCEPT"
    assert store.ro("MATCH (h:Handoff {id: $h}) RETURN h.status", {"h": res.record_id}) == [["proposed"]]
    default = submit_handoff(store, "Ana", "Bo", "auth", "Take over auth.", ["DOC-01"], LAST_DAY)
    assert store.ro("MATCH (h:Handoff {id: $h}) RETURN h.status", {"h": default.record_id}) == [["open"]]

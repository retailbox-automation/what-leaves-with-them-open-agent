"""Contract tests for the record helpers added to store.py for the write gate (both engines)."""

import os

import pytest

from honest_memory.schema import ENTITY_KEYS, PREDICATES
from honest_memory.store import Store

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def store(request):
    from falkordb import FalkorDB

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=request.param, socket_timeout=10)
    s = Store(db, f"test_records_{os.getpid()}")
    s.drop()
    s.ensure_schema()
    yield s
    s.drop()


def test_project_is_a_led_entity():
    assert ENTITY_KEYS["Project"] == "name"
    assert "Project" in PREDICATES["LEADS"].object_alt and PREDICATES["LEADS"].object == "Team"


def test_leads_accepts_a_project_object(store):
    store.merge_entity("Person", "Ana")
    store.merge_entity("Project", "Ledger Hardening", {"goal": "faster close"})
    store.create_fact("LEADS", "Ana", "Ledger Hardening", {"valid_at": 1, "source_ids": ["DOC-03"], "gate_id": "g1"},
                      object_label="Project")
    facts = store.current_facts("LEADS", obj="Ledger Hardening", object_label="Project")
    assert [(f["subject"], f["object"]) for f in facts] == [("Ana", "Ledger Hardening")]
    # without the label the Team lookup finds nothing: the edge really goes to a Project node
    assert store.current_facts("LEADS", obj="Ledger Hardening") == []
    store.add_fact_source("LEADS", "Ana", "Ledger Hardening", ["DOC-09"], object_label="Project")
    store.set_fact_props("LEADS", "Ana", "Ledger Hardening", {"count": 2}, object_label="Project")
    (fact,) = store.current_facts("LEADS", "Ana", "Ledger Hardening", object_label="Project")
    assert fact["source_ids"] == ["DOC-03", "DOC-09"] and fact["count"] == 2
    assert store.close_fact("LEADS", "Ana", "Ledger Hardening", 5, "g2", object_label="Project") == 1
    assert store.ro("MATCH (:Person)-[h:WAS]->(:Project) RETURN h.predicate, h.invalid_at") == [["LEADS", 5]]


def test_object_label_must_belong_to_the_predicate(store):
    with pytest.raises(ValueError):
        store.current_facts("OWNS", object_label="Person")
    with pytest.raises(ValueError):
        store.create_fact("OWNS", "Ana", "auth", {}, object_label="Team) DETACH DELETE (n")


def test_next_sequence_counts_per_name(store):
    assert [store.next_sequence("gate") for _ in range(3)] == [1, 2, 3]
    assert store.next_sequence("finding") == 1
    assert store.next_sequence("gate") == 4


def test_records_returns_properties_by_id(store):
    store.merge_entity("Episode", "msg-0001", {"ts": 10, "text": "hello"})
    store.merge_entity("GateDecision", "GD-1", {"outcome": "ACCEPT"})
    got = store.records("Episode", ["msg-0001", "msg-9999"])
    assert list(got) == ["msg-0001"] and got["msg-0001"]["text"] == "hello" and got["msg-0001"]["ts"] == 10
    assert list(store.records("GateDecision", ["GD-1"])) == ["GD-1"]
    assert store.records("Episode", []) == {}
    with pytest.raises(KeyError):
        store.records("Person; MATCH (n) DELETE n", ["x"])


def test_entity_names_lists_one_label(store):
    store.merge_entity("Person", "Bo")
    store.merge_entity("Person", "Ana")
    store.merge_entity("Team", "Payments")
    assert store.entity_names("Person") == ["Ana", "Bo"]


def test_link_is_idempotent_and_skips_missing_targets(store):
    store.merge_entity("Episode", "msg-0001", {"ts": 1})
    store.merge_entity("System", "auth")
    store.merge_entity("System", "ledger-db")
    store.link("Episode", "msg-0001", "MENTIONS", "System", ["auth", "ledger-db", "ghost"])
    store.link("Episode", "msg-0001", "MENTIONS", "System", ["auth"])
    rows = store.ro("MATCH (:Episode {id: 'msg-0001'})-[r:MENTIONS]->(s:System) RETURN s.name ORDER BY s.name")
    assert rows == [["auth"], ["ledger-db"]]
    store.link("Episode", "msg-0001", "MENTIONS", "System", [])  # nothing to do


def test_link_refuses_unknown_edge_types_and_labels(store):
    with pytest.raises(ValueError):
        store.link("Episode", "msg-0001", "OWNS", "System", ["auth"])
    with pytest.raises(KeyError):
        store.link("Episode", "msg-0001", "MENTIONS", "Nope", ["auth"])

"""Contract tests for store.py, run against every FalkorDB in HM_TEST_PORTS (default 6379).

Locally: HM_TEST_PORTS=6390,6393 runs both engines (6.0.2 Rust and 4.22 C, the Cloud free version).
"""

import os

import pytest

from honest_memory.schema import graph_name
from honest_memory.store import Store, epoch

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]


@pytest.fixture(params=PORTS, ids=lambda p: f"falkordb:{p}")
def store(request):
    from falkordb import FalkorDB

    db = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=request.param, socket_timeout=10)
    s = Store(db, f"test_store_{os.getpid()}")
    s.drop()
    s.ensure_schema()
    yield s
    s.drop()


def test_graph_name_rejects_injection():
    assert graph_name("acme-1") == "hm_acme-1"
    with pytest.raises(ValueError):
        graph_name("a}) DETACH DELETE n //")
    for look_alike in ("t\u0435nant", "t\u00e9nant", "\uff54enant", "a b", "", "t{x}"):  # Cyrillic e, accented e, full-width t
        with pytest.raises(ValueError):
            graph_name(look_alike)


def test_close_fact_moves_edge_to_history(store):
    for label, name in (("Person", "Ana"), ("Person", "Bo"), ("System", "billing-api")):
        store.merge_entity(label, name)
    store.create_fact("OWNS", "Ana", "billing-api", {"valid_at": epoch("2026-08-17T12:00:00Z"),
                                                     "source_ids": ["DOC-01"], "gate_id": "g1"})
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Ana"]

    moved = store.close_fact("OWNS", "Ana", "billing-api", epoch("2026-09-22T15:28:00Z"), "g2")
    store.create_fact("OWNS", "Bo", "billing-api", {"valid_at": epoch("2026-09-22T15:28:00Z"),
                                                    "source_ids": ["ADR-0009"], "gate_id": "g2"})

    assert moved == 1
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == ["Bo"]
    hist = store.ro("MATCH (p:Person)-[h:WAS]->(s:System) RETURN p.name, h.predicate, h.source_ids, h.closed_by")
    assert hist == [["Ana", "OWNS", ["DOC-01"], "g2"]]
    # The present is reachable without touching history.
    assert store.ro("MATCH (:Person {name: 'Ana'})-[:OWNS]->(s) RETURN s") == []


def test_add_fact_source_dedupes(store):
    store.merge_entity("Person", "Ana")
    store.merge_entity("System", "auth")
    store.create_fact("OWNS", "Ana", "auth", {"valid_at": 1, "source_ids": ["DOC-01"], "gate_id": "g1"})
    store.add_fact_source("OWNS", "Ana", "auth", ["DOC-01", "TCK-0033"])
    assert store.current_facts("OWNS", "Ana", "auth")[0]["source_ids"] == ["DOC-01", "TCK-0033"]


def test_tenants_are_isolated(store):
    other = Store(store.db, store.tenant + "_other")
    other.drop()
    other.merge_entity("Person", "Mallory")
    try:
        assert store.ro("MATCH (p:Person {name: 'Mallory'}) RETURN p") == []
    finally:
        other.drop()


def test_trace_records_every_query(store):
    store.trace.clear()
    store.ro("RETURN 1")
    assert len(store.trace) == 1 and store.trace[0].rows == 1

"""Ingest the whole seed-42 corpus through the extractor and the gate, on every FalkorDB in HM_TEST_PORTS."""

import json
import os
import re
import time
from collections import Counter

import pytest

from honest_memory.corpus import build, write_corpus
from honest_memory.extract import RuleExtractor
from honest_memory.gate import GateResult
from honest_memory.ingest import ingest_corpus, ingest_episode, main
from honest_memory.schema import ENTITY_KEYS, PREDICATES
from honest_memory.store import Store, epoch

PORTS = [int(p) for p in os.environ.get("HM_TEST_PORTS", "6379").split(",")]
SEED = int(os.environ.get("CORPUS_TEST_SEED", "42"))
REASONS = {"new_fact", "already_current", "newer_than_current", "new_handoff", "ended",
           "pii", "no_source", "conflicts_current", "older_than_current"}


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory):
    return write_corpus(build(SEED), tmp_path_factory.mktemp("corpus") / "corpus")


@pytest.fixture(scope="module")
def corpus(corpus_dir):
    def read(name):
        return [json.loads(line) for line in (corpus_dir / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]

    return {name: read(name) for name in ("episodes", "gold_facts", "questions")}


@pytest.fixture(scope="module", params=PORTS, ids=lambda p: f"falkordb:{p}")
def db(request):
    from falkordb import FalkorDB

    conn = FalkorDB(host=os.environ.get("HM_TEST_HOST", "localhost"), port=request.param, socket_timeout=30)
    conn.test_port = request.param
    return conn


def receipts(store):
    rows = store.ro("MATCH (g:GateDecision) RETURN g.id, g.outcome, g.reasons, g.subject, g.predicate, g.object, g.ts, "
                    "g.claim_episode, g.source_ids ORDER BY g.id")
    return [tuple(r) for r in rows]


@pytest.fixture(scope="module")
def loaded(db, corpus_dir):
    store = Store(db, f"test_ingest_{os.getpid()}")
    summary = ingest_corpus(store, corpus_dir, reset=True)
    yield store, summary
    store.drop()


@pytest.fixture(scope="module")
def store(loaded):
    return loaded[0]


@pytest.fixture(scope="module")
def summary(loaded):
    return loaded[1]


def gold_rows(corpus, **kw):
    return [g for g in corpus["gold_facts"] if all(g.get(k) == v for k, v in kw.items())]


def current_gold(corpus):
    """Gold facts that must be current typed edges: everything not closed, not refused, not a handoff record."""
    return [g for g in corpus["gold_facts"] if not g["invalid_at"] and g["predicate"] in PREDICATES
            and g.get("expected_gate", {}).get("outcome", "ACCEPT") == "ACCEPT"]


def current_edge(store, g):
    p = PREDICATES[g["predicate"]]
    for label in p.object_labels:
        found = store.current_facts(p.name, g["subject"], g["object"], object_label=label)
        if found:
            return found[0]
    return None


# ---- summary ---------------------------------------------------------------------------------------------------
def test_summary_counts_every_episode_and_gate_call(corpus, store, summary):
    assert summary["episodes"] == len(corpus["episodes"]) == store.ro("MATCH (e:Episode) RETURN count(e)")[0][0]
    assert sum(summary["outcomes"].values()) == summary["gate_calls"] == len(receipts(store))
    assert summary["outcomes"]["SUPERSEDE"] == 1  # billing-api changes owner once
    assert summary["outcomes"]["CLOSE"] == 3  # N5: the last day ends one membership and two ownerships


def test_gate_refuses_exactly_the_adversarial_claims(corpus, summary):
    refused = [g for g in corpus["gold_facts"] if g.get("expected_gate", {}).get("outcome") == "REJECT"]
    assert summary["outcomes"]["REJECT"] == len(refused) == 4
    assert set(summary["reasons"]) == REASONS
    assert (summary["reasons"]["pii"], summary["reasons"]["no_source"], summary["reasons"]["conflicts_current"],
            summary["reasons"]["older_than_current"]) == (2, 1, 1, 1)


def test_ingest_stays_under_thirty_seconds(summary):
    assert summary["seconds"] < 30


# ---- scenarios: outcomes and reasons match the gold ---------------------------------------------------------------
def test_adversarial_gold_matches_the_receipts(corpus, store):
    rows = receipts(store)
    checked = 0
    for g in corpus["gold_facts"]:
        want = g.get("expected_gate")
        if not want:
            continue
        mine = [r for r in rows if (r[3], r[4]) == (g["subject"], g["predicate"])
                and (g["predicate"] not in ("HAS_PHONE", "HAS_ADDRESS") or r[5] == "[redacted]")
                and (r[7] == g["claim_episode"] if "claim_episode" in g else (r[5], r[8]) == (g["object"], g["source_ids"]))]
        assert len(mine) == 1, g["id"]
        assert mine[0][1] == want["outcome"], g["id"]
        if "reasons" in want:
            assert set(mine[0][2]) == set(want["reasons"]), g["id"]
        checked += 1
    assert checked == 5  # S2, S3, the two S4 claims, and the S10 dependency that must be accepted


def test_s1_ownership_moves_and_history_keeps_the_old_owner(corpus, store):
    new = next(g for g in gold_rows(corpus, scenario="S1", predicate="OWNS") if not g["invalid_at"])
    old = next(g for g in gold_rows(corpus, scenario="S1", predicate="OWNS") if g["invalid_at"])
    assert [f["subject"] for f in store.current_facts("OWNS", obj="billing-api")] == [new["subject"]]
    (row,) = store.ro("MATCH (p:Person)-[h:WAS {predicate: 'OWNS'}]->(s:System {name: 'billing-api'}) "
                      "RETURN p.name, h.valid_at, h.invalid_at, h.source_ids, h.closed_by")
    assert row[:3] == [old["subject"], epoch(old["valid_at"]), epoch(old["invalid_at"])]
    assert set(row[3]) == set(old["source_ids"])
    (closer,) = store.ro("MATCH (g:GateDecision {id: $g}) RETURN g.outcome, g.subject", {"g": row[4]})
    assert closer == ["SUPERSEDE", new["subject"]]


# ---- the graph holds exactly the gold facts --------------------------------------------------------------------------------
def test_every_current_gold_fact_is_a_typed_edge(corpus, store):
    rows = current_gold(corpus)
    assert len(rows) >= 90
    for g in rows:
        edge = current_edge(store, g)
        assert edge, (g["id"], g["subject"], g["predicate"], g["object"])
        assert edge["valid_at"] == epoch(g["valid_at"]), g["id"]
        assert set(edge["source_ids"]) == set(g["source_ids"]), g["id"]
        assert edge["gate_id"].startswith("GD-")
        for key, value in g["attrs"].items():
            if key != "rank":  # rank orders people against each other; an edge only carries its own count
                assert edge[key] == value, (g["id"], key)


def test_the_graph_holds_no_current_fact_the_gold_does_not_have(corpus, store):
    want = Counter(g["predicate"] for g in current_gold(corpus))
    for name in PREDICATES:
        assert store.ro(f"MATCH ()-[r:{name}]->() RETURN count(r)")[0][0] == want[name], name


def test_every_closed_gold_fact_is_in_history(corpus, store):
    closed = [g for g in corpus["gold_facts"] if g["invalid_at"]]
    assert closed
    for g in closed:
        rows = store.ro("MATCH (a)-[h:WAS {predicate: $p}]->(b) WHERE (a.name = $s OR a.id = $s) AND "
                        "(b.name = $o OR b.id = $o) RETURN h.valid_at, h.invalid_at, h.source_ids",
                        {"p": g["predicate"], "s": g["subject"], "o": g["object"]})
        assert [r[:2] for r in rows] == [[epoch(g["valid_at"]), epoch(g["invalid_at"])]], g["id"]
        assert set(rows[0][2]) == set(g["source_ids"])


def test_worked_on_counts_match_the_work_items(corpus, store):
    rows = gold_rows(corpus, predicate="WORKED_ON")
    assert len(rows) >= 30
    for g in rows:
        edge = current_edge(store, g)
        assert edge["count"] == g["attrs"]["count"] == len(edge["source_ids"])


def test_superseded_decision_is_marked(store):
    status = dict(store.ro("MATCH (d:Decision) RETURN d.id, d.status"))
    assert [k for k, v in status.items() if v == "superseded"] == ["ADR-0004"]
    assert status["ADR-0011"] == "accepted"


def test_the_new_dependency_is_accepted_with_its_adr_as_source(corpus, store):
    (g,) = gold_rows(corpus, scenario="S10", predicate="DEPENDS_ON")
    assert current_edge(store, g)["source_ids"] == ["ADR-0013"]
    (row,) = store.ro("MATCH (g:GateDecision {predicate: 'DEPENDS_ON', subject: 'reporting', object: 'billing-api'}) "
                      "RETURN g.outcome, g.reasons")
    assert row == ["ACCEPT", ["new_fact"]]


# ---- dependencies and impact ----------------------------------------------------------------------------------------------------
def test_dependency_chain_is_four_hops_long(store):
    four = store.ro("MATCH p = (a:System)-[:DEPENDS_ON*4]->(b:System) RETURN a.name, b.name")
    assert {a for a, _ in four} == {"status-page"} and {b for _, b in four} == {"ledger-db", "auth"}
    assert store.ro("MATCH p = (a:System)-[:DEPENDS_ON*5]->(b:System) RETURN count(p)") == [[0]]


def test_graph_answers_the_current_impact_questions(corpus, store):
    impact = [q for q in corpus["questions"] if q["type"] == "impact" and q["as_of"] is None]
    assert len(impact) == 4
    owners = dict(store.ro("MATCH (p:Person)-[:OWNS]->(s:System) RETURN s.name, p.name"))
    for q in impact:
        system, depth = re.match(r"What depends on ([\w-]+) within (\d) hops", q["question"]).groups()
        rows = store.ro(f"MATCH p = (a:System)-[:DEPENDS_ON*1..{int(depth)}]->(s:System {{name: $s}}) "
                        "RETURN a.name, min(length(p))", {"s": system})
        got = {a: (owners[a], hops) for a, hops in rows}
        want = {m[1]: (m[2], int(m[4])) for m in re.finditer(r"([\w-]+) \(owner (.+?), (\w+), (\d) hops?\)", q["answer"])}
        assert got == want, q["id"]


def test_graph_answers_every_who_owns_question(corpus, store):
    owners = dict(store.ro("MATCH (p:Person)-[:OWNS]->(s:System) RETURN s.name, p.name"))
    asked = [q for q in corpus["questions"] if q["question"].startswith("Who currently owns ")]
    assert len(asked) == 9
    for q in asked:
        assert q["answer"].startswith(owners[q["question"].removeprefix("Who currently owns ").rstrip("?")]), q["id"]


# ---- records around the facts -------------------------------------------------------------------------------------------------------
def test_support_handoff_is_a_handoff_node(corpus, store):
    (g,) = gold_rows(corpus, scenario="S6")
    (row,) = store.ro("MATCH (h:Handoff)-[:FROM]->(a:Person), (h)-[:TO]->(b:Person) RETURN a.name, b.name, h.topic, h.id")
    assert row[:3] == [g["subject"], g["object"], g["attrs"]["topic"]]
    cites = store.ro("MATCH (:Handoff {id: $h})-[:CITES]->(e:Episode) RETURN e.id", {"h": row[3]})
    assert sorted(c[0] for c in cites) == sorted(g["source_ids"])


def test_episodes_link_to_what_they_refer_to_and_mention(corpus, store):
    refs = {(e["id"], r) for e in corpus["episodes"] for r in e["refs"]}
    stored = {tuple(r) for r in store.ro("MATCH (a:Episode)-[:REFERS_TO]->(b:Episode) RETURN a.id, b.id")}
    assert stored == refs and len(refs) > 30
    query = "MATCH (:Episode {id: $e})-[:MENTIONS]->(x) RETURN labels(x)[0], coalesce(x.name, x.id)"
    s1_chat = next(s for s in next(g for g in gold_rows(corpus, scenario="S1", predicate="OWNS")
                                   if not g["invalid_at"])["source_ids"] if s.startswith("msg-"))
    chat = {tuple(r) for r in store.ro(query, {"e": s1_chat})}
    adr = {tuple(r) for r in store.ro(query, {"e": "ADR-0009"})}
    assert ("System", "billing-api") in chat
    assert {("Decision", "ADR-0009"), ("System", "billing-api"), ("Team", "Payments")} <= adr


def test_entities_exist_only_because_a_document_declared_them(store):
    for label in ("Person", "Team", "System", "Decision", "Project"):
        key = ENTITY_KEYS[label]
        orphans = store.ro(f"MATCH (n:{label}) OPTIONAL MATCH (e:Episode {{channel: 'doc'}})-[:MENTIONS]->(n) "
                           f"WITH n, count(e) AS docs WHERE docs = 0 RETURN n.{key}")
        assert orphans == [], label
    counts = {label: store.ro(f"MATCH (n:{label}) RETURN count(n)")[0][0] for label in ("Person", "System", "Project")}
    assert counts == {"Person": 14, "System": 11, "Project": 5}  # + fernhill-sso and oncall-runbook (N5)


def test_a_chat_message_cannot_create_entities(db, corpus):
    s = Store(db, f"test_ingest_{os.getpid()}_chat")
    s.drop()
    s.ensure_schema()
    try:
        ex = RuleExtractor()
        ingest_episode(s, next(e for e in corpus["episodes"] if e["title"] == "Service catalog"), ex)
        stranger = {"id": "msg-9999", "ts": "2026-09-01T10:00:00Z", "channel": "chat", "author": "Zed Quux",
                    "title": "#payments", "refs": [],
                    "text": "Pushed a fix for coupon validation in billing-api; the cause was a stale cache entry."}
        (res,) = ingest_episode(s, stranger, ex)
        assert (res.outcome, res.reasons) == ("REJECT", ["unknown_entity"])
        assert not s.find_entity("Person", "Zed Quux")
    finally:
        s.drop()


def test_an_episode_dated_far_in_the_future_is_refused_and_leaves_no_trace(db, corpus):
    s = Store(db, f"test_ingest_{os.getpid()}_future")
    s.drop()
    s.ensure_schema()
    try:
        ex = RuleExtractor()
        ingest_episode(s, next(e for e in corpus["episodes"] if e["title"] == "Service catalog"), ex)
        before = s.ro("MATCH (n) RETURN count(n)")[0][0]
        runbook = {"id": "RB-99", "ts": "2030-01-01T00:00:00Z", "channel": "doc", "author": "Aiko Rademacher", "refs": [],
                   "title": "Runbook: billing-api", "text": "Runbook: billing-api (public API).\nOwner: Aiko Rademacher (Growth)."}
        with pytest.raises(ValueError, match="future"):
            ingest_episode(s, runbook, ex)
        assert s.ro("MATCH (n) RETURN count(n)")[0][0] == before  # not even the Episode node
        soon = {**runbook, "id": "RB-98", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 600))}
        assert [r.outcome for r in ingest_episode(s, soon, ex)] == ["SUPERSEDE"]  # a few minutes of clock skew is allowed
    finally:
        s.drop()


def test_a_late_episode_still_links_to_entities_a_previous_run_created(db, corpus):
    s = Store(db, f"test_ingest_{os.getpid()}_late")
    s.drop()
    s.ensure_schema()
    try:
        ingest_episode(s, next(e for e in corpus["episodes"] if e["title"] == "Service catalog"), RuleExtractor())
        late = {"id": "msg-9998", "ts": "2026-09-02T10:00:00Z", "channel": "chat", "author": "Sana Petrov",
                "title": "#payments", "refs": [], "text": "billing-api is quiet today."}
        assert ingest_episode(s, late, RuleExtractor()) == []  # a new extractor, an existing graph
        got = s.ro("MATCH (:Episode {id: 'msg-9998'})-[:MENTIONS]->(x) RETURN labels(x)[0], x.name")
        assert got == [["System", "billing-api"]]
    finally:
        s.drop()


def test_no_personal_data_in_receipts_or_edges(corpus, store):
    secrets = [g["object"] for g in corpus["gold_facts"] if g["predicate"] in ("HAS_PHONE", "HAS_ADDRESS")]
    assert len(secrets) == 2
    dump = json.dumps(store.ro("MATCH (g:GateDecision) RETURN properties(g)")) + \
        json.dumps(store.ro("MATCH ()-[r]->() RETURN properties(r)"))
    for value in secrets:
        assert value not in dump and value.split(",")[0] not in dump


# ---- repeatability ----------------------------------------------------------------------------------------------------------------------
def test_reset_ingest_twice_gives_identical_receipts(db, corpus_dir, store):
    again = Store(db, f"test_ingest_{os.getpid()}_again")
    try:
        ingest_corpus(again, corpus_dir, reset=True)
        first = receipts(again)
        ingest_corpus(again, corpus_dir, reset=True)
        assert receipts(again) == first == receipts(store)
        assert len(first) > 150
    finally:
        again.drop()


def test_ingest_without_reset_confirms_and_changes_no_fact(db, corpus_dir):
    s = Store(db, f"test_ingest_{os.getpid()}_noreset")
    owners = "MATCH (p:Person)-[r:OWNS]->(s:System) RETURN p.name, s.name, r.source_ids, r.valid_at ORDER BY s.name"
    try:
        ingest_corpus(s, corpus_dir, reset=True)
        before = s.ro(owners)
        second = ingest_corpus(s, corpus_dir)
        assert s.ro(owners) == before and second["outcomes"]["CONFIRM"] > 0 and second["outcomes"]["SUPERSEDE"] == 0
    finally:
        s.drop()


# ---- the single-episode entry point and the CLI -----------------------------------------------------------------------------------------
def test_ingest_episode_returns_gate_results_in_order(db, corpus):
    s = Store(db, f"test_ingest_{os.getpid()}_one")
    s.drop()
    s.ensure_schema()
    try:
        ex = RuleExtractor()
        results = [r for e in corpus["episodes"][:3] for r in ingest_episode(s, e, ex)]
        assert results and all(isinstance(r, GateResult) for r in results)
        assert [r.outcome for r in results][:9] == ["ACCEPT"] * 9  # the nine owners in the service catalog
        assert s.find_entity("System", "billing-api")
    finally:
        s.drop()


def test_cli_prints_a_summary_table(db, corpus_dir, capsys):
    tenant = f"test_ingest_{os.getpid()}_cli"
    try:
        argv = ["--corpus", str(corpus_dir), "--tenant", tenant, "--reset", "--host", "localhost", "--port", str(db.test_port)]
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert all(word in out for word in ("ACCEPT", "REJECT", "no_source", "pii", "conflicts_current"))
        assert re.search(r"\b\d+ episodes\b", out)
    finally:
        Store(db, tenant).drop()

"""Day-1 spike: measure FalkorDB behaviour the design depends on.

Run: python spike/spike_local.py --host localhost --port 6390
Prints one JSON object per question; nothing here calls an LLM or embedding API.
"""
import argparse
import json
import random
import statistics
import time

from falkordb import FalkorDB


def timed(graph, query, params=None, runs=20):
    samples = []
    result = None
    for _ in range(runs):
        t0 = time.perf_counter()
        result = graph.query(query, params or {})
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), result


def build_temporal(db, name, nodes, edges, closed_share, structural, seed=7):
    """Random graph where `closed_share` of facts are no longer valid.

    structural=False: every fact is :REL, closed ones carry invalid_at.
    structural=True:  current facts are :REL, closed ones are :REL_HIST.
    """
    g = db.select_graph(name)
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph(name)
    rnd = random.Random(seed)
    g.query("UNWIND range(0, $n - 1) AS i CREATE (:E {id: i})", {"n": nodes})
    g.query("CREATE INDEX FOR (e:E) ON (e.id)")
    batch = []
    for _ in range(edges):
        closed = rnd.random() < closed_share
        batch.append({"a": rnd.randrange(nodes), "b": rnd.randrange(nodes), "closed": closed})
        if len(batch) == 5000:
            _write_edges(g, batch, structural)
            batch = []
    if batch:
        _write_edges(g, batch, structural)
    return g


def _write_edges(g, batch, structural):
    if structural:
        g.query("UNWIND $rows AS r WITH r WHERE NOT r.closed "
                "MATCH (a:E {id: r.a}), (b:E {id: r.b}) CREATE (a)-[:REL {valid_at: 1}]->(b)", {"rows": batch})
        g.query("UNWIND $rows AS r WITH r WHERE r.closed "
                "MATCH (a:E {id: r.a}), (b:E {id: r.b}) CREATE (a)-[:REL_HIST {valid_at: 1, invalid_at: 2}]->(b)", {"rows": batch})
    else:
        g.query("UNWIND $rows AS r MATCH (a:E {id: r.a}), (b:E {id: r.b}) "
                "CREATE (a)-[:REL {valid_at: 1, invalid_at: CASE WHEN r.closed THEN 2 ELSE null END}]->(b)", {"rows": batch})


def q3_temporal(db, nodes, edges, hops):
    out = {}
    starts = list(range(0, 50))
    variants = {
        # current and closed facts share one edge type; filter over the path after the traversal
        "property_filter_path": (False, f"MATCH p = (s:E {{id: $s}})-[:REL*1..{hops}]->(t) "
                                        "WHERE all(x IN relationships(p) WHERE x.invalid_at IS NULL) "
                                        "RETURN count(DISTINCT t)"),
        # same layout; the optimizer moves a property filter on the var-length variable into the traversal
        "property_filter_pushdown": (False, f"MATCH (s:E {{id: $s}})-[r:REL*1..{hops}]->(t) "
                                            "WHERE r.invalid_at IS NULL RETURN count(DISTINCT t)"),
        # closed facts live under another edge type, so the traversal never sees them
        "structural": (True, f"MATCH (s:E {{id: $s}})-[:REL*1..{hops}]->(t) RETURN count(DISTINCT t)"),
    }
    built = {}
    for label, (structural, q) in variants.items():
        name = "spike_q3_struct" if structural else "spike_q3_prop"
        if name not in built:
            built[name] = build_temporal(db, name, nodes, edges, 0.3, structural)
        g = built[name]
        per_start, counts = [], []
        for s in starts:
            ms, res = timed(g, q, {"s": s}, runs=5)
            per_start.append(ms)
            counts.append(res.result_set[0][0])
        mem = db.connection.execute_command("GRAPH.MEMORY", "USAGE", name)
        out[label] = {
            "median_ms": round(statistics.median(per_start), 2),
            "p90_ms": round(sorted(per_start)[int(len(per_start) * 0.9)], 2),
            "reachable_sum": sum(counts),
            "plan": str(g.explain(q, {"s": 0})).splitlines(),
            "memory_usage": mem,
        }
    return out


def q4_vector(db):
    g = db.select_graph("spike_q4")
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph("spike_q4")
    rnd = random.Random(1)
    dim = 64
    rows = [{"id": i, "v": [rnd.uniform(-1, 1) for _ in range(dim)], "sys": i % 9} for i in range(1000)]
    g.query("UNWIND range(0, 8) AS s CREATE (:System {id: s})")
    g.query("UNWIND $rows AS r MATCH (s:System {id: r.sys}) "
            "CREATE (d:Doc {id: r.id, emb: vecf32(r.v)})-[:MENTIONS]->(s)", {"rows": rows})
    g.query(f"CREATE VECTOR INDEX FOR (d:Doc) ON (d.emb) OPTIONS {{dimension: {dim}, similarityFunction: 'cosine'}}")
    # Index creation is asynchronous: wait until it reports OPERATIONAL before querying.
    t0, status = time.perf_counter(), None
    while time.perf_counter() - t0 < 10:
        idx_rows = g.query("CALL db.indexes() YIELD label, status RETURN label, status").result_set
        status = next((r[1] for r in idx_rows if r[0] == "Doc"), None)
        if status == "OPERATIONAL":
            break
        time.sleep(0.05)
    index_wait_ms = round((time.perf_counter() - t0) * 1000, 1)
    q = ("CALL db.idx.vector.queryNodes('Doc', 'emb', 5, vecf32($v)) YIELD node, score "
         "MATCH (node)-[:MENTIONS]->(s:System) RETURN node.id, s.id, score")
    ms, res = timed(g, q, {"v": rows[3]["v"]}, runs=20)
    return {"median_ms": round(ms, 2), "top_hit": res.result_set[0], "rows": len(res.result_set),
            "index_status": status, "index_wait_ms": index_wait_ms}


def q5_isolation(db):
    a, b = db.select_graph("spike_q5_tenant_a"), db.select_graph("spike_q5_tenant_b")
    for g in (a, b):
        try:
            g.delete()
        except Exception:
            pass
    a, b = db.select_graph("spike_q5_tenant_a"), db.select_graph("spike_q5_tenant_b")
    a.query("CREATE (:Fact {who: 'a'})")
    b.query("CREATE (:Fact {who: 'b'})")
    seen_from_a = [r[0] for r in a.query("MATCH (f:Fact) RETURN f.who").result_set]
    return {"seen_from_a": seen_from_a, "leak": "b" in seen_from_a, "graphs": sorted(db.list_graphs())}


def q6_algorithms(db):
    g = db.select_graph("spike_q4")
    procs = [r[0] for r in g.query("CALL dbms.procedures() YIELD name RETURN name").result_set]
    return {"procedures": sorted(procs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=6390)
    ap.add_argument("--nodes", type=int, default=10_000)
    ap.add_argument("--edges", type=int, default=50_000)
    ap.add_argument("--hops", type=int, default=4)
    ap.add_argument("--only", default="q3,q4,q5,q6", help="comma-separated subset of questions to run")
    args = ap.parse_args()
    db = FalkorDB(host=args.host, port=args.port)
    only = set(args.only.split(","))
    if "q3" in only:
        print(json.dumps({"q3_temporal": q3_temporal(db, args.nodes, args.edges, args.hops)}, default=str))
    if "q5" in only:
        print(json.dumps({"q5_isolation": q5_isolation(db)}))
    if "q4" in only:
        print(json.dumps({"q4_vector": q4_vector(db)}, default=str))
    if "q6" in only:
        print(json.dumps({"q6_algorithms": q6_algorithms(db)}))


if __name__ == "__main__":
    main()

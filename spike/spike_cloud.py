"""Day-1 spike against FalkorDB Cloud: round-trip latency and bytes per fact.

Run: FALKORDB_PASSWORD=... python spike/spike_cloud.py --host <host> --port <port> [--tls]
"""
import argparse
import json
import os
import statistics
import time

from falkordb import FalkorDB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--user", default="falkordb")
    ap.add_argument("--tls", action="store_true")
    ap.add_argument("--facts", type=int, default=10_000)
    a = ap.parse_args()
    db = FalkorDB(host=a.host, port=a.port, username=a.user,
                  password=os.environ["FALKORDB_PASSWORD"], ssl=a.tls)
    g = db.select_graph("spike_cloud")
    try:
        g.delete()
    except Exception:
        pass
    g = db.select_graph("spike_cloud")

    rtt = []
    for _ in range(50):
        t0 = time.perf_counter()
        g.query("RETURN 1")
        rtt.append((time.perf_counter() - t0) * 1000)
    info = db.connection.execute_command("GRAPH.CONFIG", "GET", "TIMEOUT")
    modules = db.connection.execute_command("MODULE", "LIST")
    procs = sorted(r[0] for r in g.query("CALL dbms.procedures() YIELD name RETURN name").result_set)

    # A fact as the design stores it: two entity nodes and one typed edge with provenance.
    rows = [{"a": f"person-{i % 500}", "b": f"system-{i % 90}", "src": f"ep-{i}", "gate": f"g-{i}"}
            for i in range(a.facts)]
    g.query("CREATE INDEX FOR (e:Entity) ON (e.key)")
    for i in range(0, len(rows), 2000):
        g.query("UNWIND $rows AS r MERGE (a:Entity {key: r.a}) MERGE (b:Entity {key: r.b}) "
                "CREATE (a)-[:OWNS {valid_at: 1760000000, invalid_at: null, source_id: r.src, "
                "gate_id: r.gate, confidence: 0.9}]->(b)", {"rows": rows[i:i + 2000]})
    mem = db.connection.execute_command("GRAPH.MEMORY", "USAGE", "spike_cloud")
    mem = dict(zip(mem[0::2], mem[1::2]))
    total_mb = float(mem.get("total_graph_sz_mb", 0))
    print(json.dumps({
        "rtt_ms_p50": round(statistics.median(rtt), 1),
        "rtt_ms_p90": round(sorted(rtt)[44], 1),
        "timeout_config": info,
        "modules": modules,
        "algorithms": [p for p in procs if p.startswith("algo.")],
        "facts": a.facts,
        "graph_mb": total_mb,
        "bytes_per_fact": round(total_mb * 1024 * 1024 / a.facts) if total_mb else None,
        "facts_fit_in_100mb": int(100 / total_mb * a.facts) if total_mb else None,
    }, default=str))
    g.delete()


if __name__ == "__main__":
    main()

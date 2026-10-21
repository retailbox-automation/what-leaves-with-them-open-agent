"""After `OPTIONAL MATCH ... WITH n, count(...) AS c WHERE ...`, a filter on a node that the next MATCH binds sees
that node as null on FalkorDB 6.0.2, so every row is dropped. 4.22 returns the row. Found while writing
single_source (honest_memory/departure.py), which now passes the superseded decisions in as a list.

    python spike/with_where_repro.py [port ...]      (default: 6390 6393)
"""
import sys

from falkordb import FalkorDB

HEAD = "MATCH (d:Dec {id: 'd1'}) OPTIONAL MATCH (:Dec)-[s:S]->(d) WITH d, count(s) AS c WHERE c = 0 "
QUERIES = {
    "filter on the new node x":             HEAD + "MATCH (x)-[r:D]->(d) WHERE x.name = 'ana' RETURN count(r)",
    "x IS NOT NULL":                        HEAD + "MATCH (x)-[r:D]->(d) WHERE x.name IS NOT NULL RETURN count(r)",
    "control: filter on the new edge r":    HEAD + "MATCH (x)-[r:D]->(d) WHERE r.v > 0 RETURN count(r)",
    "control: no WHERE after the WITH":     "MATCH (d:Dec {id: 'd1'}) OPTIONAL MATCH (:Dec)-[s:S]->(d) WITH d, count(s) AS c "
                                            "MATCH (x)-[r:D]->(d) WHERE x.name = 'ana' RETURN count(r)",
    "control: count(*), no OPTIONAL MATCH": "MATCH (d:Dec {id: 'd1'}) WITH d, count(*) AS c WHERE c = 1 "
                                            "MATCH (x)-[r:D]->(d) WHERE x.name = 'ana' RETURN count(r)",
    "workaround: filter in a later WITH":   HEAD + "MATCH (x)-[r:D]->(d) WITH x, r WHERE x.name = 'ana' RETURN count(r)",
}

for port in [int(p) for p in sys.argv[1:]] or [6390, 6393]:
    g = FalkorDB(host="localhost", port=port, socket_timeout=10).select_graph("with_where_repro")
    g.query("CREATE (:P {name: 'ana'})-[:D {v: 5}]->(:Dec {id: 'd1'})")
    try:
        for name, q in QUERIES.items():
            print(port, f"{name:<38}", g.query(q).result_set)  # expected [[1]] every time
    finally:
        g.delete()

"""algo.betweenness on FalkorDB 4.22 and 6.0.2: direction, sampling, and how far each engine is from exact.

    python spike/betweenness_repro.py [port ...]      (default: 6390 6393)

1. Direction: p -> a and p -> b (p has only outgoing edges, like a Person with OWNS and WORKED_ON). Read
   undirected, p sits on the only a-b path; the procedure follows edge direction and gives p 0, and the config
   accepts no direction option.
2. Exactness: a 5-node path written both ways (a-p-b-q-c), exact scores 6, 8, 6 for p, b, q (ordered pairs).
3. Sampling: on a 30-node graph, compare with exact Brandes for the default config, samplingSize = node count,
   and a few seeds.
"""
import random
import sys
from collections import defaultdict, deque

from falkordb import FalkorDB


def exact(nodes, out):
    """Brandes over a directed adjacency (ordered pairs)."""
    bc = dict.fromkeys(nodes, 0.0)
    for s in nodes:
        order, preds, sigma, dist = [], defaultdict(list), dict.fromkeys(nodes, 0), dict.fromkeys(nodes, -1)
        sigma[s], dist[s], queue = 1, 0, deque([s])
        while queue:
            v = queue.popleft()
            order.append(v)
            for w in out[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    preds[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        for w in reversed(order):
            for v in preds[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    return bc


def scores(g, config="{}"):
    return dict(g.query(f"CALL algo.betweenness({config}) YIELD node, score RETURN node.n, score").result_set)


for port in [int(p) for p in sys.argv[1:]] or [6390, 6393]:
    db = FalkorDB(host="localhost", port=port, socket_timeout=30)
    g = db.select_graph("betweenness_repro")
    try:
        g.query("CREATE (a:X {n:'a'}), (b:X {n:'b'}), (p:X {n:'p'}), (p)-[:L]->(a), (p)-[:L]->(b)")
        print(port, "1. p has only outgoing edges:", scores(g))
        try:
            g.query("CALL algo.betweenness({direction: 'both'}) YIELD node RETURN node")
        except Exception as exc:
            print(port, "   direction option:", exc)
        g.delete()

        g.query("CREATE (a:X {n:'a'}), (p:X {n:'p'}), (b:X {n:'b'}), (q:X {n:'q'}), (c:X {n:'c'}), "
                "(a)-[:L]->(p), (p)-[:L]->(a), (p)-[:L]->(b), (b)-[:L]->(p), (b)-[:L]->(q), (q)-[:L]->(b), "
                "(q)-[:L]->(c), (c)-[:L]->(q)")
        got = scores(g, "{samplingSize: 5, samplingSeed: 1}")
        print(port, "2. path a-p-b-q-c both ways, expect p=6 b=8 q=6:", {k: got[k] for k in "pbq"})
        g.delete()

        rng = random.Random(30)
        nodes = [f"n{i}" for i in range(30)]
        und = {frozenset((nodes[rng.randrange(i)], nodes[i])) for i in range(1, 30)}
        while len(und) < 45:
            und.add(frozenset(rng.sample(nodes, 2)))
        edges = [tuple(e) for e in und] + [tuple(reversed(tuple(e))) for e in und]
        out = defaultdict(list)
        for x, y in edges:
            out[x].append(y)
        want = exact(nodes, out)
        g.query("UNWIND $ns AS n CREATE (:X {n: n})", {"ns": nodes})
        g.query("UNWIND $es AS e MATCH (x:X {n: e[0]}), (y:X {n: e[1]}) CREATE (x)-[:L]->(y)", {"es": [list(e) for e in edges]})
        for config in ("{}", "{samplingSize: 30, samplingSeed: 1}", "{samplingSize: 30, samplingSeed: 2}",
                       "{samplingSize: 3000, samplingSeed: 1}"):
            got = scores(g, config)
            err = max(abs(got[n] - want[n]) for n in nodes)
            print(port, f"3. 30 nodes {config:<38} max |error| vs exact = {err:.1f}, "
                        f"sum ratio = {sum(got.values()) / sum(want.values()):.2f}")
    finally:
        try:
            g.delete()
        except Exception:
            pass

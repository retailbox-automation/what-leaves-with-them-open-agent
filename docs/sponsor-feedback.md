# Notes for the FalkorDB team

Things we ran into while building on FalkorDB during Graph Hacks, each with a reproduction. Engines tested side by side: FalkorDB 4.22.0 (C engine, the version FalkorDB Cloud free runs) and 6.0.2 (Rust engine, `falkordb/falkordb:latest`), Python client `falkordb==1.8.0`. Scripts are in `spike/`.

## 1. A variable-length bound cannot be a query parameter
The hackathon guide's blast-radius query uses `-[:DEPENDS_ON*1..$depth]->`. On both engines this fails, and the error points somewhere else:

```python
g.query("MATCH (x:S {n:'a'})-[:D*1..$d]->(y) RETURN y.n", {"d": 3})
# ResponseError: Encountered unhandled type in inlined properties.
g.query("MATCH (x:S {n:'a'})-[:D*1..3]->(y) RETURN y.n")   # works: ['b', 'c']
g.query("MATCH (x:S {n:$n}) RETURN x.n", {"n": "a"})        # control: parameters work elsewhere
```
Workaround we use: validate depth as an integer 1-4 in code and format it into the pattern. Related but not the same: FalkorDB #1916 (a parameter as inlined pattern properties, same error text).

## 2. Cloud free runs 4.22, Docker `latest` is 6.0.x, and they differ
Code written against one does not always run on the other:
- Filtering a variable-length relationship as a list, `WHERE all(e IN r WHERE ...)`: 6.0.2 `Type mismatch: expected List but was Path` (see #3296).
- `db.idx.fulltext.createNodeIndex(label, field, ...)`: fails on 6.0.2 with `Received N arguments ..., expected at most 1` (#2540); works on 4.22.
- `algo.AStar` and `db.idx.cch.query` exist on 4.22 / Cloud, not on 6.0.2.
- Right after `CREATE VECTOR INDEX`, 4.22 answers `db.idx.vector.queryNodes` with `Invalid arguments for procedure` until the index is OPERATIONAL (~55 ms here); 6.0.2 answers during construction.
What helped: a test matrix on both engines from day one.

## 3. Cloud free: plain RESP only, and the client waits forever on TLS
The free instance endpoint answers plain connections (round trip p50 42 ms, p90 150 ms from the US east coast). With `ssl=True` the connection never completes; `falkordb-py` has no default socket timeout, so the call hangs with no error. A one-line note on the Connect screen, and a default timeout in the client, would save the first hour.

## 4. Keeping history as an edge type beats filtering on a timestamp
For "edges that change over time" (asked in #questions on 11.10): 20k nodes, 200k edges, 30 % closed, 4 hops, 50 start nodes. Same answer in all three layouts.

| Layout | 6.0.2 median | 4.22 median |
|---|---|---|
| Filter over the path: `WHERE all(x IN relationships(p) WHERE x.invalid_at IS NULL)` | 15.22 ms | 9.27 ms |
| Filter on the relationship variable right after MATCH: `WHERE r.invalid_at IS NULL` (pushed into the traversal) | 4.34 ms | 2.17 ms |
| Closed facts moved to another edge type (`WAS`) | 1.48 ms | 1.32 ms |

Reproduce: `python spike/spike_local.py --nodes 20000 --edges 200000 --hops 4`.

## 5. Graphiti's FalkorDB driver on 6.0.2
graphiti-core 0.30.2 schedules `build_indices_and_constraints()` on every `clone(database=...)`; on 6.0.2 every one of those tasks fails (41 of 41, the fulltext procedure from item 2) and the failure only shows as "Task exception was never retrieved". With 20 groups and 200 scoped calls, both engines return 101 `MaxConnectionsError` (getzep/graphiti #1934). The `n:A|B` label expression it emits for multi-label filters is rejected by both parsers (#1892). We went with direct Cypher instead.

## 6. `algo.betweenness` follows edge direction, and there is no option to ignore it
In a knowledge graph most edges point one way (Person -[:OWNS]-> System, Person -[:WORKED_ON]-> System). A person
with only outgoing edges sits on many undirected paths but scores 0 on both engines; the config has no
direction key (6.0.2: `Unknown parameter: direction`, 4.22: `betweenness configuration contains unknown key`).

```python
g.query("CREATE (a:X {n:'a'}), (b:X {n:'b'}), (p:X {n:'p'}), (p)-[:L]->(a), (p)-[:L]->(b)")
g.query("CALL algo.betweenness({}) YIELD node, score RETURN node.n, score")   # p: 0.0 on both engines
```
Workaround we use (`honest_memory/departure.py`, `bridges`): copy the people-teams-systems projection into a
temporary graph with every link written both ways, run the procedure there, delete the graph. A `direction:
'both'` option (as `algo.SPpaths` has with `relDirection`) would remove the copy.

## 7. `algo.betweenness` samples sources: exact on 6.0.2 only with `samplingSize` >= node count, never exact on 4.22
Reproduce: `python spike/betweenness_repro.py`. Same graphs, scores compared with an exact Brandes computation:

| Graph, config | 6.0.2 | 4.22 (Cloud free) |
|---|---|---|
| 5-node path written both ways, `samplingSize: 5` (exact p, b, q = 6, 8, 6) | 6, 8, 6 | 11, 6, 5 |
| 30 nodes, default config | sum of scores 0.48 of exact (default sample is smaller than the graph, unscaled) | max error 60 |
| 30 nodes, `samplingSize: 30`, seed 1 / seed 2 | exact / exact | max error 53 / 77 |
| 30 nodes, `samplingSize: 3000` | exact | sum 2.67x exact |

So on 6.0.2 the default silently drops part of the graph (the sum of scores halves at 30 nodes), and on 4.22 the
scores change with the seed even when the sample covers every node. What helped: pass `samplingSize` = node count,
run two seeds, and treat the result as exact only when they agree; otherwise report the mean of several seeded
runs as an estimate (that is what `bridges` does, and it says so in its answer). On the demo corpus the ranking
survives the estimate (top bridge 290.28 exact on 6.0.2, 230.53 mean of 8 runs on 4.22). A note in the docs on
the default sample size and the sampling scheme would have saved the comparison work.

## 8. 6.0.2: a filter after `OPTIONAL MATCH ... WITH x, count(...) AS c WHERE ...` sees the next MATCH's node as null
Reproduce: `python spike/with_where_repro.py`.

```cypher
MATCH (d:Dec {id: 'd1'}) OPTIONAL MATCH (:Dec)-[s:S]->(d) WITH d, count(s) AS c WHERE c = 0
MATCH (x)-[r:D]->(d) WHERE x.name = 'ana' RETURN count(r)
-- 6.0.2: 0    4.22: 1    (graph: (:P {name: 'ana'})-[:D]->(:Dec {id: 'd1'}))
```
`WHERE x.name IS NOT NULL` also gives 0 on 6.0.2; a filter on the new edge `r` works; removing the `WHERE` after
the `WITH`, or replacing the `OPTIONAL MATCH` + `count(s)` with `count(*)`, gives 1. Workaround: filter in a later
`WITH ... WHERE`, or (what we did for "decisions not superseded") compute the excluded ids in a separate query and
pass them as a list parameter. It cost us an empty `single_source` answer on 6.0.2 only, which the two-engine test
matrix caught.
